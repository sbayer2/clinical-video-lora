"""Machine annotation stage, cloud path (ADC-012): claude-opus-4-8 reads
sampled frames + transcript slice + paralinguistic measurements per window
and fills the encounter record schema.

Purpose per the ADC decision: TEST SCAFFOLDING for the conditioning pathway
and the schema, not corpus production. Machine records are unmixable from
human ones by construction — JSONL with an `annotator: "machine"` wrapper,
never the annotator's SQLite DB. See derive/common.py for shared machinery
and derive/local_annotate.py for the local-model path.

Run:  .venv/bin/python -m derive.ai_annotate <media...> [--window-mins 5]
Writes scripts/out/ai_annotations/<film>.jsonl (gitignored).
"""

import argparse
import base64
import json
import sys
import time
import uuid
from pathlib import Path

import anthropic
import jsonschema

from .common import (
    CACHE_DIR,
    OUT_DIR,
    REPO_ROOT,
    SYSTEM_PROMPT,
    extract_frame_jpegs,
    load_schema,
    media_duration,
    prompt_sha,
    sanitized_schema,
    sh,
    sha256_file,
    wrap_record,
)

MODEL = "claude-opus-4-8"


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
    pauses = [(b["start"] - a["end"]) / 16000 for a, b in zip(ts, ts[1:])]
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
    for t, jpg in frames:
        content.append({"type": "text", "text": f"Frame at {t}s:"})
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.standard_b64encode(jpg).decode(),
            },
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

    schema = load_schema()
    validator = jsonschema.Draft202012Validator(schema)
    # The API key lives in a locked-down file, NOT in shell profiles: an
    # exported ANTHROPIC_API_KEY is inherited by every shell including the
    # one that launches Claude Code, which then bills sessions to the key
    # instead of the user's subscription. Learned the expensive way.
    key_file = Path.home() / ".config" / "anthropic-derive.key"
    api_key = key_file.read_text().strip() if key_file.exists() else None
    client = anthropic.Anthropic(api_key=api_key)  # None -> env/default chain
    print(f"model: {MODEL}, prompt: {prompt_sha()}")

    total_usage = {"input": 0, "output": 0}
    for path in args.media:
        session_id = path.stem
        duration = media_duration(path)
        print(f"\n=== {session_id} ({duration/60:.1f} min)")
        transcript = transcribe_cached(path)
        source_hash = sha256_file(path)
        out_path = OUT_DIR / f"{session_id}.jsonl"
        records = []
        window = args.window_mins * 60
        n_windows = max(1, round(duration / window))
        for i in range(n_windows):
            t0, t1 = i * duration / n_windows, (i + 1) * duration / n_windows
            para = paralinguistics(path, t0, t1, transcript)
            frames = extract_frame_jpegs(path, t0, t1)
            try:
                record, usage = annotate_window(
                    client, schema, validator, session_id, t0, t1, transcript, para, frames
                )
            except Exception as e:  # report per window, keep going
                print(f"  window {t0:.0f}-{t1:.0f}s FAILED: {e}")
                continue
            total_usage["input"] += usage.input_tokens
            total_usage["output"] += usage.output_tokens
            records.append(wrap_record(
                record, model=MODEL, tool="derive.ai_annotate",
                source=path, source_hash=source_hash, t0=t0, t1=t1,
            ))
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
