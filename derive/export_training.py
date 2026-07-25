"""Export layer v2 (plan section 5 module 6, ADC-013/014): transform machine
annotation records into adapter training pairs.

The pair is the plan's core conditioning claim in miniature:
  input  = situation + the read (affect, acuity, relationship, register, why)
  output = what the clinician actually said in that clip (transcript slice)

v2 methodological fixes over the PoC exporter:
- **Dedup on completion text AND on span overlap** (IoU > 0.6 against
  accepted spans within a session): multi-pass annotation must not feed the
  same target text under variant prompts — that trains memorization.
- **Film-held-out validation** (--holdout, default beck_richard): random
  splits leak near-duplicate slices across the boundary; a held-out film is
  the honest generalization test.

Labels are machine-generated (ADC-012: consistency, not truth).

Run:  .venv/bin/python -m derive.export_training [--holdout beck_richard]
Writes data/adapter/{train,valid}.jsonl and modulation_eval.jsonl.
"""

import argparse
import hashlib
import json
import random
from pathlib import Path

from .common import CACHE_DIR, OUT_DIR, REPO_ROOT

DATA_DIR = REPO_ROOT / "data" / "adapter"
MIN_CLIP_S, MAX_CLIP_S = 2.0, 120.0
MIN_TEXT_CHARS = 60
SPAN_IOU_DEDUP = 0.6
SHUFFLE_SEED = 20260725

SYSTEM = (
    "You are an experienced urgent care clinician. Given the situation and "
    "your read of the patient, say your next lines to the patient in the "
    "selected register. Respond with the spoken lines only."
)

MODULATION_READS = [
    {"affect_observed": "frightened", "acuity": "low", "register_selected": "warm",
     "why": "fear is disproportionate to findings; needs steadying before facts"},
    {"affect_observed": "dismissive", "acuity": "moderate", "register_selected": "firm",
     "why": "minimizing real risk; needs the stakes stated plainly"},
    {"affect_observed": "calm", "acuity": "low", "register_selected": "brisk",
     "why": "informed and comfortable; efficiency respects their time"},
]


def transcript_slice(session_id: str, t0: float, t1: float) -> str | None:
    cache = CACHE_DIR / f"{session_id}.transcript.json"
    if not cache.exists():
        return None
    segs = json.loads(cache.read_text())["segments"]
    parts = [s["text"].strip() for s in segs if s["end"] > t0 and s["start"] < t1]
    return " ".join(parts).strip() or None


def prompt_from_read(read: dict, context: str) -> str:
    return (
        f"Situation: {context}\n"
        f"Patient affect observed: {read['affect_observed']}\n"
        f"Acuity: {read['acuity']}\n"
        f"Prior relationship: {read['prior_relationship']}\n"
        f"Register selected: {read['register_selected']}\n"
        f"Why this register: {read['why']}\n"
        f"Say your next lines to the patient:"
    )


def span_iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdout", default="beck_richard",
                        help="session_id held out entirely for validation")
    args = parser.parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    examples: dict[str, list[dict]] = {}  # session_id -> examples
    accepted_spans: dict[str, list[tuple[float, float]]] = {}
    seen_completions: set[str] = set()
    skipped = {"span": 0, "no_text": 0, "dup_text": 0, "dup_span": 0}

    # Sort wrappers per session by clip start so overlap-dedup keeps the
    # first (arbitrary but deterministic) of each overlapping cluster.
    wrappers = []
    for jl in sorted(OUT_DIR.glob("*.jsonl")):
        for line in jl.read_text().splitlines():
            wrappers.append(json.loads(line))
    wrappers.sort(key=lambda w: (w["source_file"], w["record"]["clip"]["t_start"]))

    for w in wrappers:
        r = w["record"]
        t0, t1 = r["clip"]["t_start"], r["clip"]["t_end"]
        if not (MIN_CLIP_S <= t1 - t0 <= MAX_CLIP_S):
            skipped["span"] += 1
            continue
        session_id = Path(w["source_file"]).stem
        spans = accepted_spans.setdefault(session_id, [])
        if any(span_iou((t0, t1), s) > SPAN_IOU_DEDUP for s in spans):
            skipped["dup_span"] += 1
            continue
        text = transcript_slice(session_id, t0, t1)
        if not text or len(text) < MIN_TEXT_CHARS:
            skipped["no_text"] += 1
            continue
        text_key = hashlib.sha256(text.encode()).hexdigest()
        if text_key in seen_completions:
            skipped["dup_text"] += 1
            continue
        seen_completions.add(text_key)
        spans.append((t0, t1))
        context = r.get("context") or f"clinical encounter, {r['segment_class']} moment"
        examples.setdefault(session_id, []).append({
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt_from_read(r["read"], context)},
                {"role": "assistant", "content": text},
            ]
        })

    if args.holdout not in examples:
        raise SystemExit(f"holdout '{args.holdout}' has no pairs; sessions: {sorted(examples)}")
    valid = examples.pop(args.holdout)
    train = [e for sess in examples.values() for e in sess]
    random.Random(SHUFFLE_SEED).shuffle(train)
    random.Random(SHUFFLE_SEED).shuffle(valid)

    (DATA_DIR / "train.jsonl").write_text("\n".join(json.dumps(e) for e in train) + "\n")
    (DATA_DIR / "valid.jsonl").write_text("\n".join(json.dumps(e) for e in valid) + "\n")

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
    print(f"skipped: {skipped}")
    print(f"modulation probes: {len(probes)}")


if __name__ == "__main__":
    main()
