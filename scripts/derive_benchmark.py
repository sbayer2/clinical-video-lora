"""Derive-layer smoke benchmark (ADC-008): wall-clock per stage on one real
encounter-length recording, on this machine.

Each stage is timed independently with its own input scope and reported as a
multiple of realtime, so honest extrapolation is possible even where a stage
runs on a slice. Failures are reported per stage, never hidden.

Run:  .venv/bin/python scripts/derive_benchmark.py <media-file> [--minutes 15]
Writes scripts/out/derive_benchmark.json (gitignored) and prints a table.
"""

import argparse
import json
import subprocess
import time
from pathlib import Path

import numpy as np

OUT_DIR = Path(__file__).resolve().parent / "out"
SER_MODEL_URL = "https://zenodo.org/record/6221127/files/w2v2-L-robust-12.6bc4a7fd-1.1.0.zip"
FACE_TASK_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)


def timed(results: dict, name: str, scope: str, fn) -> dict | None:
    t0 = time.perf_counter()
    try:
        extra = fn() or {}
        results[name] = {"scope": scope, "seconds": round(time.perf_counter() - t0, 2),
                         "status": "ok", **extra}
        return results[name]
    except Exception as e:  # report, don't abort the benchmark
        results[name] = {"scope": scope, "seconds": round(time.perf_counter() - t0, 2),
                         "status": f"FAILED: {type(e).__name__}: {e}"}
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("media", type=Path)
    parser.add_argument("--minutes", type=float, default=15.0)
    args = parser.parse_args()
    OUT_DIR.mkdir(exist_ok=True)
    secs = args.minutes * 60.0
    work = OUT_DIR / "bench_work"
    work.mkdir(exist_ok=True)
    wav16 = work / "audio16k.wav"
    results: dict = {"input": str(args.media), "minutes": args.minutes}

    # -- extract: first N minutes to 16 kHz mono (ASR/VAD/SER/prosody input)
    def extract():
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(args.media), "-t", str(secs),
             "-ac", "1", "-ar", "16000", str(wav16)],
            check=True,
        )
        return {"bytes": wav16.stat().st_size}
    timed(results, "extract_audio", f"{args.minutes:g} min", extract)

    # torchaudio 2.11 removed audio I/O without torchcodec; load once with
    # soundfile and hand tensors/arrays to every stage that needs them.
    import soundfile as sf
    audio_np, _sr = sf.read(str(wav16), dtype="float32")
    speech_ts: list = []

    # -- VAD (Silero)
    def vad():
        nonlocal speech_ts
        import torch
        from silero_vad import get_speech_timestamps, load_silero_vad
        model = load_silero_vad()
        speech_ts = get_speech_timestamps(
            torch.from_numpy(audio_np), model, sampling_rate=16000
        )
        speech_secs = sum(t["end"] - t["start"] for t in speech_ts) / 16000
        return {"speech_segments": len(speech_ts), "speech_seconds": round(speech_secs, 1)}
    timed(results, "vad_silero", f"{args.minutes:g} min", vad)

    # -- ASR (mlx-whisper large-v3-turbo); model download cached after first run
    transcript_text = ""

    def asr():
        nonlocal transcript_text
        import mlx_whisper
        out = mlx_whisper.transcribe(
            str(wav16), path_or_hf_repo="mlx-community/whisper-large-v3-turbo"
        )
        transcript_text = out["text"]
        return {"chars": len(transcript_text), "segments": len(out.get("segments", []))}
    timed(results, "asr_mlx_whisper_turbo", f"{args.minutes:g} min", asr)

    # -- Forced alignment (torchaudio MMS_FA) on a 5-min slice
    def align():
        import torch
        import torchaudio
        from torchaudio.pipelines import MMS_FA as bundle

        device = "cpu"  # MPS unsupported for this op chain in places; CPU is the honest floor
        model = bundle.get_model(with_star=False).to(device)
        slice_secs = min(300.0, secs)
        wave = torch.from_numpy(audio_np[: int(16000 * slice_secs)]).unsqueeze(0)
        # Rough transcript slice proportional to time — a smoke benchmark of
        # throughput, not of alignment quality.
        words_all = [
            "".join(c for c in w.lower() if "a" <= c <= "z" or c == "'")
            for w in transcript_text.split()
        ]
        words_all = [w for w in words_all if w]
        words = words_all[: max(1, int(len(words_all) * slice_secs / secs))]
        dictionary = bundle.get_dict(star=None)
        targets = [dictionary[c] for w in words for c in w]
        with torch.inference_mode():
            emission, _ = model(wave.to(device))
            torchaudio.functional.forced_align(
                torch.log_softmax(emission, dim=-1),
                torch.tensor([targets], dtype=torch.int32, device=device),
                blank=0,
            )
        return {"slice_seconds": slice_secs, "words": len(words)}
    timed(results, "forced_align_mms", "5 min slice", align)

    # -- Prosody: pYIN F0 + RMS + pause stats
    def prosody():
        import librosa
        y, sr = librosa.load(str(wav16), sr=16000)
        f0, _, voiced_prob = librosa.pyin(
            y, fmin=60, fmax=400, sr=sr, frame_length=1024, hop_length=160
        )
        rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=160)[0]
        pauses = [
            (b["start"] - a["end"]) / 16000
            for a, b in zip(speech_ts, speech_ts[1:])
        ] if len(speech_ts) > 1 else []
        return {
            "voiced_fraction": round(float(np.nanmean(voiced_prob)), 3),
            "f0_median": round(float(np.nanmedian(f0)), 1),
            "rms_frames": len(rms),
            "pauses_over_1s": sum(p > 1.0 for p in pauses),
        }
    timed(results, "prosody_pyin_rms", f"{args.minutes:g} min", prosody)

    # -- SER: audeering wav2vec2 arousal/dominance/valence, 2 s hops on speech
    def ser():
        import audeer
        import audonnx
        cache = OUT_DIR / "ser_model"
        if not (cache / "model.onnx").exists():
            archive = audeer.download_url(SER_MODEL_URL, str(OUT_DIR), verbose=False)
            audeer.extract_archive(archive, str(cache), verbose=False)
        model = audonnx.load(str(cache))
        sig = audio_np
        hop, win = 2 * 16000, 2 * 16000
        n = 0
        for start in range(0, max(1, len(sig) - win), hop):
            model(sig[start : start + win], 16000)
            n += 1
        return {"windows": n}
    timed(results, "ser_audeering_advd", f"{args.minutes:g} min, 2s hops", ser)

    # -- Video: MediaPipe face landmarks at 10 fps sampled frames
    def face():
        import urllib.request

        import cv2
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        task_path = OUT_DIR / "face_landmarker.task"
        if not task_path.exists():
            urllib.request.urlretrieve(FACE_TASK_URL, task_path)
        options = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(task_path)),
            output_face_blendshapes=True,
            running_mode=vision.RunningMode.VIDEO,
        )
        landmarker = vision.FaceLandmarker.create_from_options(options)
        cap = cv2.VideoCapture(str(args.media))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, round(fps / 10.0))
        frames = detected = 0
        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok or idx / fps >= secs:
                break
            if idx % step == 0:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                res = landmarker.detect_for_video(image, int(idx / fps * 1000))
                frames += 1
                detected += bool(res.face_landmarks)
            idx += 1
        cap.release()
        return {"frames_processed": frames, "faces_detected": detected}
    timed(results, "face_mediapipe_10fps", f"{args.minutes:g} min @10fps", face)

    # -- Change-point: ruptures PELT on a 1 Hz feature stream
    def changepoint():
        import ruptures as rpt
        n = int(secs)
        rng = np.random.default_rng(0)  # placeholder stream, timing-representative
        stream = rng.normal(size=(n, 6))
        algo = rpt.Pelt(model="rbf", min_size=10).fit(stream)
        breaks = algo.predict(pen=10)
        return {"breakpoints": len(breaks)}
    timed(results, "changepoint_pelt", f"{args.minutes:g} min @1Hz x6", changepoint)

    # -- report
    (OUT_DIR / "derive_benchmark.json").write_text(json.dumps(results, indent=2))
    print(f"\ninput: {args.media.name}, first {args.minutes:g} min\n")
    header = f"{'stage':<26} {'scope':<20} {'seconds':>8}  {'x realtime':>10}  status"
    print(header)
    print("-" * len(header))
    audio_stages = ["vad_silero", "asr_mlx_whisper_turbo", "prosody_pyin_rms",
                    "ser_audeering_advd", "changepoint_pelt"]
    total_audio = 0.0
    for name, r in results.items():
        if not isinstance(r, dict) or "seconds" not in r:
            continue
        scope_secs = 300.0 if "slice" in r["scope"] else secs
        rtf = scope_secs / r["seconds"] if r["seconds"] > 0 else float("inf")
        status = r["status"] if r["status"] != "ok" else "ok"
        print(f"{name:<26} {r['scope']:<20} {r['seconds']:>8.1f}  {rtf:>9.1f}x  {status}")
        if name in audio_stages and r["status"] == "ok":
            total_audio += r["seconds"]
    print(f"\naudio-only derive total (measured stages): {total_audio:.0f}s "
          f"for {secs:.0f}s of audio ({secs / total_audio:.1f}x realtime)"
          if total_audio else "")
    print(f"json: {OUT_DIR / 'derive_benchmark.json'}")


if __name__ == "__main__":
    main()
