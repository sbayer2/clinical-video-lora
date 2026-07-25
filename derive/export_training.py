"""Export layer (plan section 5 module 6, ADC-013): transform machine
annotation records into adapter training pairs.

The pair is the plan's core conditioning claim in miniature:
  input  = situation + the read (affect, acuity, relationship, register, why)
  output = what the clinician actually said in that clip (transcript slice)

So the adapter learns "delivery conditioned on read" — the register-
modulation pathway the section-6 evals then probe. Labels are machine-
generated (ADC-012: consistency, not truth); this is proof-of-concept
scaffolding, and the pairs carry a known impurity: the films are not
diarized, so slices can include patient lines.

Run:  .venv/bin/python -m derive.export_training
Writes data/adapter/{train,valid}.jsonl (mlx-lm chat format, gitignored)
and data/adapter/modulation_eval.jsonl (section-6 modulation probes).
"""

import hashlib
import json
import random
from pathlib import Path

from .common import CACHE_DIR, OUT_DIR, REPO_ROOT

DATA_DIR = REPO_ROOT / "data" / "adapter"
MIN_CLIP_S, MAX_CLIP_S = 2.0, 120.0
MIN_TEXT_CHARS = 40
VALID_FRACTION = 0.1
SPLIT_SEED = 20260725

SYSTEM = (
    "You are an experienced urgent care clinician. Given the situation and "
    "your read of the patient, say your next lines to the patient in the "
    "selected register. Respond with the spoken lines only."
)

# Contrasting read variants for the modulation eval: same situation, the
# read swapped. A working adapter should shift register; a caricature
# applies one register uniformly (plan section 6, "the important one").
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


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    examples, seen = [], set()
    skipped = {"span": 0, "no_text": 0, "dup": 0}

    for jl in sorted(OUT_DIR.glob("*.jsonl")):
        for line in jl.read_text().splitlines():
            w = json.loads(line)
            r = w["record"]
            t0, t1 = r["clip"]["t_start"], r["clip"]["t_end"]
            if not (MIN_CLIP_S <= t1 - t0 <= MAX_CLIP_S):
                skipped["span"] += 1
                continue
            session_id = Path(w["source_file"]).stem
            text = transcript_slice(session_id, t0, t1)
            if not text or len(text) < MIN_TEXT_CHARS:
                skipped["no_text"] += 1
                continue
            context = r.get("context") or f"clinical encounter, {r['segment_class']} moment"
            user = prompt_from_read(r["read"], context)
            key = hashlib.sha256((user + text).encode()).hexdigest()
            if key in seen:
                skipped["dup"] += 1
                continue
            seen.add(key)
            examples.append({
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user},
                    {"role": "assistant", "content": text},
                ]
            })

    if len(examples) < 20:
        raise SystemExit(f"only {len(examples)} usable pairs — generate more annotation passes first")

    random.Random(SPLIT_SEED).shuffle(examples)
    n_valid = max(4, int(len(examples) * VALID_FRACTION))
    valid, train = examples[:n_valid], examples[n_valid:]
    (DATA_DIR / "train.jsonl").write_text("\n".join(json.dumps(e) for e in train) + "\n")
    (DATA_DIR / "valid.jsonl").write_text("\n".join(json.dumps(e) for e in valid) + "\n")

    # Modulation eval: situations from validation, each crossed with the
    # contrasting reads. base_read prompt included for the blind comparison.
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

    print(f"pairs: {len(train)} train / {len(valid)} valid "
          f"(skipped: {skipped['span']} span, {skipped['no_text']} no-text, {skipped['dup']} dup)")
    print(f"modulation probes: {len(probes)} ({len(valid[:6])} situations x {len(MODULATION_READS)} reads)")
    print(f"wrote {DATA_DIR.relative_to(REPO_ROOT)}/{{train,valid,modulation_eval}}.jsonl")


if __name__ == "__main__":
    main()
