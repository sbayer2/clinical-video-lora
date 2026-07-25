"""Machine annotation stage, local path (ADC-012 extension): Qwen3-Omni via
mlx-vlm annotates the same windows as the cloud path — but from NATIVE
modalities: raw window audio + sampled frames in one forward pass, no
transcript bolted on. This is both the cost comparison the cloud path
motivates and the only architecture compatible with PHI once real capture
exists (patient media can never go to a cloud API).

Runs in the mlx-vlm venv, not the project venv:

    ~/omlx/.venv/bin/pip install jsonschema          # once
    ~/omlx/.venv/bin/python -m derive.local_annotate media/archive/*.mp4

First run downloads the model (~22 GB) from
mlx-community/Qwen3-Omni-30B-A3B-Instruct-4bit. Records land beside the
cloud path's output as <film>.qwen3omni.jsonl with the same provenance
wrapper — model field distinguishes annotators; same canonical schema gate.
"""

import argparse
import json
import sys
import tempfile
import time
import uuid
import wave
from pathlib import Path

import jsonschema
import numpy as np

from .common import (
    CACHE_DIR,
    OUT_DIR,
    REPO_ROOT,
    SYSTEM_PROMPT,
    extract_audio_window,
    extract_frame_jpegs,
    extract_json,
    generation_schema,
    load_schema,
    media_duration,
    prompt_sha,
    sha256_file,
    wrap_record,
)

MODEL_ID = "mlx-community/Qwen3-Omni-30B-A3B-Instruct-4bit"
MAX_TOKENS = 2000


def load_model():
    from mlx_vlm import load

    print(f"loading {MODEL_ID} (first run downloads ~22 GB)...")
    return load(MODEL_ID)


def build_prompt(processor, config, session_id: str, t0: float, t1: float,
                 schema: dict, n_images: int, n_audios: int) -> str:
    from mlx_vlm.prompt_utils import apply_chat_template

    task = (
        SYSTEM_PROMPT
        + f"\n\nsession_id: {session_id}\nThe JSON schema:\n{json.dumps(schema)}"
        + f"\n\nYou are hearing the actual audio and seeing frames from the"
          f" window {t0:.0f}s to {t1:.0f}s of the session clock. Timestamps in"
          f" the audio are relative to the window start; convert to session"
          f" clock by adding {t0:.0f}."
    )
    try:
        return apply_chat_template(
            processor, config, task, num_images=n_images, num_audios=n_audios
        )
    except TypeError:  # older mlx-vlm without num_audios
        return apply_chat_template(processor, config, task, num_images=n_images)


def read_wav_f32(path: Path) -> np.ndarray:
    """16 kHz mono PCM16 WAV -> float32 array. mlx-vlm 0.6.3's generate()
    hands raw path strings to the HF processor (bypassing its own
    load_audio), so we load the samples ourselves."""
    with wave.open(str(path), "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return data.astype(np.float32) / 32768.0


def annotate_window(model, processor, config, schema, validator, session_id,
                    t0, t1, frame_paths, audio_path, raw_dump: Path):
    from mlx_vlm import generate
    from mlx_vlm.structured import build_json_schema_logits_processor

    gen_schema = generation_schema(schema)
    prompt = build_prompt(
        processor, config, session_id, t0, t1, gen_schema,
        n_images=len(frame_paths), n_audios=1,
    )
    # Constrained decoding (llguidance): the decoder masks any token that
    # would violate the schema, so malformed JSON, <|im_start|> loops, and
    # empty outputs are structurally impossible. The generation schema
    # excludes pipeline-stamped fields (record_id/annotated_at) — leaving
    # them required forces small models into degenerate UUID zero-collapse
    # (observed: record_id "r-0000..."). The prompt embeds the same schema
    # so instruction and constraint agree. Canonical validation still runs
    # afterward.
    tokenizer = getattr(processor, "tokenizer", processor)
    lp = build_json_schema_logits_processor(tokenizer, gen_schema)
    started = time.perf_counter()
    result = generate(
        model, processor, prompt,
        image=[str(p) for p in frame_paths],
        audio=[read_wav_f32(audio_path)],
        max_tokens=MAX_TOKENS,
        logits_processors=[lp],
        verbose=False,
    )
    elapsed = time.perf_counter() - started
    text = result.text if hasattr(result, "text") else str(result)
    raw_dump.write_text(text)

    record = extract_json(text)
    record["record_id"] = str(uuid.uuid4())
    record["annotated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    errors = sorted(validator.iter_errors(record), key=lambda e: list(e.absolute_path))
    if errors:
        raise RuntimeError(
            "failed canonical validation: " + "; ".join(e.message for e in errors[:5])
        )
    return record, elapsed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("media", type=Path, nargs="+")
    parser.add_argument("--window-mins", type=float, default=5.0)
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    schema = load_schema()
    validator = jsonschema.Draft202012Validator(schema)
    model, processor = load_model()
    config = model.config
    print(f"model loaded; prompt: {prompt_sha()}")

    for path in args.media:
        session_id = path.stem
        duration = media_duration(path)
        print(f"\n=== {session_id} ({duration/60:.1f} min)")
        source_hash = sha256_file(path)
        out_path = OUT_DIR / f"{session_id}.qwen3omni.jsonl"
        records = []
        window = args.window_mins * 60
        n_windows = max(1, round(duration / window))
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for i in range(n_windows):
                t0, t1 = i * duration / n_windows, (i + 1) * duration / n_windows
                audio = extract_audio_window(path, t0, t1, tmp / f"w{i}.wav")
                frame_paths = []
                for t, jpg in extract_frame_jpegs(path, t0, t1):
                    fp = tmp / f"w{i}_f{t}.jpg"
                    fp.write_bytes(jpg)
                    frame_paths.append(fp)
                raw_dump = CACHE_DIR / f"{session_id}.w{i}.qwen3omni.raw.txt"
                raw_dump.parent.mkdir(parents=True, exist_ok=True)
                try:
                    record, elapsed = annotate_window(
                        model, processor, config, schema, validator,
                        session_id, t0, t1, frame_paths, audio, raw_dump,
                    )
                except Exception as e:  # report per window, keep going
                    print(f"  window {t0:.0f}-{t1:.0f}s FAILED: {e}")
                    continue
                records.append(wrap_record(
                    record, model=MODEL_ID, tool="derive.local_annotate",
                    source=path, source_hash=source_hash, t0=t0, t1=t1,
                ))
                r = record
                print(
                    f"  {t0:6.0f}-{t1:6.0f}s -> {r['segment_class']:<21} "
                    f"clip {r['clip']['t_start']:.0f}-{r['clip']['t_end']:.0f}s  "
                    f"affect={r['read']['affect_observed']}, "
                    f"register={r['read']['register_selected']}  ({elapsed:.0f}s)"
                )
        out_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n")
        print(f"  wrote {len(records)} record(s) to {out_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
