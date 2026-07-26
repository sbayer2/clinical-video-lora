"""Export layer v3 (ADC-015 Arm B): Opus-curated selections -> training pairs.

Differences from export v2 (export_training.py):
- **Completions are the curator's speaker-attributed clinician_lines**, not
  raw transcript slices. v2 completions were undiarized, which taught the
  adapter to speak patient lines (ADC-013/014); v3 trains only on text the
  curator attributed to the clinician.
- Selections filtered to speaker_attribution == "clinician" and
  confidence in {high, medium}; drops are counted, never silent.
- Situation text is joined from the overlapping v2 machine record in the
  same film (max span IoU), since the curator emits reads, not scene
  descriptions; unjoinable spans get a generic fallback and are counted.

Retained from v2: completion-text dedup, span-IoU dedup, film-held-out
validation split, prompt format, modulation probes.

Run:  .venv/bin/python -m derive.export_curated [--holdout beck_richard]
Writes data/adapter_v3/{train,valid}.jsonl and modulation_eval.jsonl.
"""

import argparse
import hashlib
import json
import random
from pathlib import Path

from .common import OUT_DIR, REPO_ROOT
from .export_training import (
    MAX_CLIP_S,
    MIN_CLIP_S,
    MIN_TEXT_CHARS,
    MODULATION_READS,
    SPAN_IOU_DEDUP,
    SYSTEM,
    prompt_from_read,
    span_iou,
)

CURATION_DIR = REPO_ROOT / "scripts" / "out" / "opus_curation"
DATA_DIR = REPO_ROOT / "data" / "adapter_v3"
SHUFFLE_SEED = 20260726
CONTEXT_JOIN_MIN_IOU = 0.2


def load_context_index() -> dict[str, list[tuple[float, float, str]]]:
    """session_id -> [(t0, t1, context)] from the v2 machine records."""
    index: dict[str, list[tuple[float, float, str]]] = {}
    for jl in sorted(OUT_DIR.glob("*.jsonl")):
        for line in jl.read_text().splitlines():
            w = json.loads(line)
            r = w["record"]
            context = r.get("context")
            if not context:
                continue
            index.setdefault(Path(w["source_file"]).stem, []).append(
                (r["clip"]["t_start"], r["clip"]["t_end"], context))
    return index


def join_context(index, session_id: str, t0: float, t1: float) -> str | None:
    best, best_iou = None, CONTEXT_JOIN_MIN_IOU
    for a0, a1, context in index.get(session_id, []):
        iou = span_iou((t0, t1), (a0, a1))
        if iou > best_iou:
            best, best_iou = context, iou
    return best


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdout", default="beck_richard")
    args = parser.parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    context_index = load_context_index()
    examples: dict[str, list[dict]] = {}
    accepted_spans: dict[str, list[tuple[float, float]]] = {}
    seen_completions: set[str] = set()
    skipped = {"not_clinician": 0, "low_confidence": 0, "span": 0,
               "short_text": 0, "dup_text": 0, "dup_span": 0}
    fallback_context = 0

    records = []
    for jl in sorted(CURATION_DIR.glob("*.curation.jsonl")):
        for line in jl.read_text().splitlines():
            records.append(json.loads(line))
    records.sort(key=lambda r: (r["session_id"], r["t_start"]))

    for r in records:
        if r["speaker_attribution"] != "clinician":
            skipped["not_clinician"] += 1
            continue
        if r["confidence"] == "low":
            skipped["low_confidence"] += 1
            continue
        t0, t1 = r["t_start"], r["t_end"]
        if not (MIN_CLIP_S <= t1 - t0 <= MAX_CLIP_S):
            skipped["span"] += 1
            continue
        session_id = r["session_id"]
        spans = accepted_spans.setdefault(session_id, [])
        if any(span_iou((t0, t1), s) > SPAN_IOU_DEDUP for s in spans):
            skipped["dup_span"] += 1
            continue
        text = r["clinician_lines"].strip()
        if len(text) < MIN_TEXT_CHARS:
            skipped["short_text"] += 1
            continue
        text_key = hashlib.sha256(text.encode()).hexdigest()
        if text_key in seen_completions:
            skipped["dup_text"] += 1
            continue
        seen_completions.add(text_key)
        spans.append((t0, t1))
        context = join_context(context_index, session_id, t0, t1)
        if context is None:
            context = "A clinical encounter in progress."
            fallback_context += 1
        examples.setdefault(session_id, []).append({
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt_from_read(r["read"], context)},
                {"role": "assistant", "content": text},
            ]
        })

    if args.holdout not in examples:
        raise SystemExit(f"holdout '{args.holdout}' has no pairs; "
                         f"sessions: {sorted(examples)}")
    valid = examples.pop(args.holdout)
    train = [e for sess in examples.values() for e in sess]
    random.Random(SHUFFLE_SEED).shuffle(train)
    random.Random(SHUFFLE_SEED).shuffle(valid)

    (DATA_DIR / "train.jsonl").write_text(
        "\n".join(json.dumps(e) for e in train) + "\n")
    (DATA_DIR / "valid.jsonl").write_text(
        "\n".join(json.dumps(e) for e in valid) + "\n")

    probes = []
    for e in valid[:6]:
        situation = e["messages"][1]["content"].split("\n")[0]
        for variant in MODULATION_READS:
            read = {**variant, "prior_relationship": "none"}
            probes.append({
                "situation": situation,
                "read": read,
                "prompt": prompt_from_read(read, situation.removeprefix("Situation: ")),
            })
    (DATA_DIR / "modulation_eval.jsonl").write_text(
        "\n".join(json.dumps(p) for p in probes) + "\n")

    per_film = {s: len(v) for s, v in examples.items()}
    print(f"train: {len(train)} pairs from {len(examples)} films {per_film}")
    print(f"valid (held-out {args.holdout}): {len(valid)} pairs")
    print(f"skipped: {skipped}; fallback contexts: {fallback_context}")
    print(f"modulation probes: {len(probes)}")


if __name__ == "__main__":
    main()
