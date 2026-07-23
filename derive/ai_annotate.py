"""Machine annotation stage (ADC-012): a multimodal model watches clip
windows and fills the encounter record schema.

Purpose per the ADC decision: this is TEST SCAFFOLDING for the conditioning
pathway and the schema, not corpus production. An AI annotating tape can only
read appearances — the clinician's privileged read is exactly what it cannot
supply — so machine records are kept unmixable from human ones by
construction: they are written to their own JSONL with an `annotator:
"machine"` wrapper and provenance, never into the annotator's SQLite DB.

Per film: transcribe once (mlx-whisper, cached), then per ~5-minute window
extract sampled frames (ffmpeg) + a light paralinguistic summary (Silero VAD
pauses, RMS, speech rate), and ask the model to select the single most
annotation-worthy moment and emit one schema-valid record. Every record is
validated against the canonical schema/encounter_record.schema.json — the
same gate the human annotator UI uses; one retry with the validation errors
fed back.

Run:  .venv/bin/python -m derive.ai_annotate <media...> [--window-mins 5]
Writes scripts/out/ai_annotations/<film>.jsonl (gitignored).
"""

import argparse
import base64
import copy
import hashlib
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import anthropic
import jsonschema

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "schema" / "encounter_record.schema.json"
OUT_DIR = REPO_ROOT / "scripts" / "out" / "ai_annotations"
CACHE_DIR = OUT_DIR / "cache"

MODEL = "claude-opus-4-8"
FRAMES_PER_WINDOW = 4
FRAME_WIDTH = 640

SYSTEM_PROMPT = """\
You are annotating clinical encounter footage for a research corpus on
clinician communication ("delivery conditioned on clinical read"). You will
see sampled frames, a transcript slice, and paralinguistic measurements from
one window of a longer film.

Your task: select the single most annotation-worthy communication moment in
this window and describe it as one record following the provided JSON schema.
Definitions:
- "read": what the clinician appeared to be perceiving at the moment
  (patient acuity, observable affect) and the delivery register they selected
  in response.
- "read.why": what in the patient's presentation drove that register choice.
- "move.description": what the clinician did communicatively and what they
  appeared to be going for.
- "move.discriminating_feature": what made this the right call rather than a
  plausible alternative.
- "in_frame_response": observable patient shift after the move, if any.
- "self_rating.worked": whether the move visibly worked (0 or 1);
  "confidence" in [0,1] — be honest, you are inferring from tape.
- "schema_gap": what mattered in this moment that the schema fields could
  NOT capture. Write "nothing" only if true.

Constraints:
- clip.t_start and clip.t_end are in seconds on the film's own clock and
  must lie within this window's bounds; keep the clip tight around the
  moment (typically 10-90 seconds).
- capture is "recording". session_id is given to you. Omit record_id and
  annotated_at; they are stamped by the pipeline.
- context must be de-identified: no names, no dates, no facility names.
- You are annotating APPEARANCES on tape, not a clinician's inner state.
  Where the schema asks for the read, report the read as evidenced by
  observable behavior, and let your confidence reflect that limit.
- Prefer non-routine moments (de-escalation, reassurance, bad-news,
  refusal-conversion, instruction-delivery) over routine when present.

Respond with the JSON record only.\
"""


def sh(args: list[str]) -> bytes:
    return subprocess.run(args, check=True, capture_output=True).stdout


def media_duration(path: Path) -> float:
    out = sh(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)])
    return float(json.loads(out)["format"]["duration"])


def blake3_or_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def transcribe_cached(path: Path) -> dict:
    """mlx-whisper transcript with segment timestamps, cached to disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{path.stem}.transcript.json"
    if cache.exists():
        return json.loads(cache.read_text())
    import mlx_whisper

    out = mlx_whisper.transcribe(
        str(path), path_or_hf_repo="mlx-community/whisper-large-v3-turbo"
    )
    slim = {
        "segments": [
            {"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"]}
            for s in out.get("segments", [])
        ]
    }
    cache.write_text(json.dumps(slim))
    return slim


def paralinguistics(path: Path, t0: float, t1: float, transcript: dict) -> dict:
    """Light per-window summary: pauses (Silero VAD), RMS spread, speech rate."""
    import numpy as np
    import soundfile as sf
    import torch
    from silero_vad import get_speech_timestamps, load_silero_vad

    wav = CACHE_DIR / f"{path.stem}.{int(t0)}.wav"
    sh(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", str(t0), "-t", str(t1 - t0), "-i", str(path),
        "-ac", "1", "-ar", "16000", str(wav)])
    audio, _ = sf.read(str(wav), dtype="float32")
    wav.unlink()

    model = load_silero_vad()
    ts = get_speech_timestamps(torch.from_numpy(audio), model, sampling_rate=16000)
    pauses = [
        (b["start"] - a["end"]) / 16000 for a, b in zip(ts, ts[1:])
    ]
    frame = 1600
    n = len(audio) // frame
    rms = np.sqrt((audio[: n * frame].reshape(n, frame) ** 2).mean(axis=1) + 1e-12)
    words = sum(
        len(s["text"].split())
        for s in transcript["segments"]
        if s["start"] >= t0 and s["end"] <= t1
    )
    speech_secs = sum(t["end"] - t["start"] for t in ts) / 16000
    return {
        "speech_seconds": round(speech_secs, 1),
        "pauses_over_1s": sum(p > 1.0 for p in pauses),
        "longest_pause_s": round(max(pauses), 1) if pauses else 0.0,
        "rms_db_p20_p80": [
            round(20 * float(np.log10(np.percentile(rms, q) + 1e-9)), 1) for q in (20, 80)
        ],
        "words_per_min_speech": round(words / (speech_secs / 60), 0) if speech_secs else 0,
    }


def extract_frames(path: Path, t0: float, t1: float) -> list[tuple[float, str]]:
    """Evenly sampled JPEG frames as (timestamp, base64)."""
    frames = []
    for i in range(FRAMES_PER_WINDOW):
        t = t0 + (t1 - t0) * (i + 0.5) / FRAMES_PER_WINDOW
        jpg = sh(["ffmpeg", "-hide_banner", "-loglevel", "error",
                  "-ss", str(t), "-i", str(path), "-frames:v", "1",
                  "-vf", f"scale={FRAME_WIDTH}:-2", "-f", "image2", "-c:v", "mjpeg", "-"])
        frames.append((round(t, 1), base64.standard_b64encode(jpg).decode()))
    return frames


def sanitized_schema(schema: dict) -> dict:
    """Copy of the canonical schema safe for output_config.format: strip
    keywords structured outputs doesn't accept (minLength, if/then allOf).
    Canonical validation still runs afterward — this only shapes decoding."""
    s = copy.deepcopy(schema)

    def strip(node):
        if isinstance(node, dict):
            node.pop("minLength", None)
            node.pop("allOf", None)
            node.pop("$schema", None)
            node.pop("$id", None)
            for v in node.values():
                strip(v)
        elif isinstance(node, list):
            for v in node:
                strip(v)

    strip(s)
    return s


def window_content(session_id, t0, t1, transcript, para, frames):
    lines = [
        f"Film: {session_id} — window {t0:.0f}s to {t1:.0f}s of the session clock.",
        f"Paralinguistics for this window: {json.dumps(para)}",
        "Transcript slice (start-end seconds: text):",
    ]
    for s in transcript["segments"]:
        if s["end"] < t0 or s["start"] > t1:
            continue
        lines.append(f"  {s['start']:.0f}-{s['end']:.0f}: {s['text'].strip()}")
    content = [{"type": "text", "text": "\n".join(lines)}]
    for t, b64 in frames:
        content.append({"type": "text", "text": f"Frame at {t}s:"})
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
        })
    return content


def annotate_window(client, schema, validator, session_id, t0, t1, transcript, para, frames):
    content = window_content(session_id, t0, t1, transcript, para, frames)
    request = {
        "model": MODEL,
        "max_tokens": 16000,
        "thinking": {"type": "adaptive"},
        "system": SYSTEM_PROMPT + f"\n\nsession_id: {session_id}\nThe JSON schema:\n"
        + json.dumps(schema),
        "messages": [{"role": "user", "content": content}],
    }
    try:
        response = client.messages.create(
            **request,
            output_config={"format": {"type": "json_schema", "schema": sanitized_schema(schema)}},
        )
    except anthropic.BadRequestError:
        # Structured-output schema restrictions are a moving target; the
        # canonical validation loop below is the real gate.
        response = client.messages.create(**request)

    for attempt in range(2):
        text = next(b.text for b in response.content if b.type == "text")
        record = json.loads(text)
        record["record_id"] = str(uuid.uuid4())
        record["annotated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        errors = sorted(validator.iter_errors(record), key=lambda e: list(e.absolute_path))
        if not errors:
            return record, response.usage
        if attempt == 1:
            raise RuntimeError(
                "record failed canonical validation twice: "
                + "; ".join(e.message for e in errors[:5])
            )
        detail = "\n".join(
            f"- {'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in errors
        )
        response = client.messages.create(
            **{**request, "messages": request["messages"] + [
                {"role": "assistant", "content": text},
                {"role": "user", "content":
                    "That record failed schema validation:\n" + detail
                    + "\nEmit the corrected JSON record only."},
            ]},
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("media", type=Path, nargs="+")
    parser.add_argument("--window-mins", type=float, default=5.0)
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    schema = json.loads(SCHEMA_PATH.read_text())
    validator = jsonschema.Draft202012Validator(schema)
    client = anthropic.Anthropic()
    prompt_sha = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:16]

    total_usage = {"input": 0, "output": 0}
    for path in args.media:
        session_id = path.stem
        duration = media_duration(path)
        print(f"\n=== {session_id} ({duration/60:.1f} min)")
        transcript = transcribe_cached(path)
        source_hash = blake3_or_sha256(path)
        out_path = OUT_DIR / f"{session_id}.jsonl"
        records = []
        window = args.window_mins * 60
        n_windows = max(1, round(duration / window))
        for i in range(n_windows):
            t0, t1 = i * duration / n_windows, (i + 1) * duration / n_windows
            para = paralinguistics(path, t0, t1, transcript)
            frames = extract_frames(path, t0, t1)
            try:
                record, usage = annotate_window(
                    client, schema, validator, session_id, t0, t1, transcript, para, frames
                )
            except Exception as e:  # report per window, keep going
                print(f"  window {t0:.0f}-{t1:.0f}s FAILED: {e}")
                continue
            total_usage["input"] += usage.input_tokens
            total_usage["output"] += usage.output_tokens
            records.append({
                "annotator": "machine",
                "model": MODEL,
                "prompt_sha256": prompt_sha,
                "tool": {"name": "derive.ai_annotate", "version": "0.1.0"},
                "source_file": path.name,
                "source_sha256": source_hash,
                "window": {"t0": round(t0, 1), "t1": round(t1, 1)},
                "record": record,
            })
            r = record
            print(
                f"  {t0:6.0f}-{t1:6.0f}s -> {r['segment_class']:<21} "
                f"clip {r['clip']['t_start']:.0f}-{r['clip']['t_end']:.0f}s  "
                f"affect={r['read']['affect_observed']}, register={r['read']['register_selected']}"
            )
        out_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n")
        print(f"  wrote {len(records)} record(s) to {out_path.relative_to(REPO_ROOT)}")

    print(f"\ntokens: {total_usage['input']} in / {total_usage['output']} out")


if __name__ == "__main__":
    sys.exit(main())
