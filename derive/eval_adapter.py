"""Challenge evals for the v2 adapter (ADC-014) — section-6 discipline.

Batteries, all base-vs-adapter on identical prompts:

1. Modulation (held-out film situations x 3 contrasting reads): mean
   pairwise word-Jaccard across the three outputs per situation — LOWER =
   more register modulation; uniform = caricature.
2. OOD challenge (eval/ood_scenarios.json — modern urgent-care situations,
   physician-edited) x same 3 reads. Memorization fails loudly here:
   film-era content injected into 2026 urgent care.
3. Memorization metric: fraction of generated 8-grams found verbatim in
   the training completions (corpus n-gram overlap). Loop-rate: outputs
   where any 6-gram repeats 3+ times.
4. Fabrication probe: numbers in output absent from prompt.
5. Blind A/B transcript dump (unlabeled, shuffled; key at the end) for
   human register-fit judgment.

Success criteria stated in ADC-014 BEFORE this ran. Run:
  ~/omlx/.venv/bin/python -m derive.eval_adapter --adapter data/adapter/lora_v2_best
"""

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path

from .common import REPO_ROOT

DATA_DIR = REPO_ROOT / "data" / "adapter"
OOD_PATH = REPO_ROOT / "eval" / "ood_scenarios.json"
BASE_MODEL = "mlx-community/Qwen3-8B-4bit"
MAX_TOKENS = 160
SYSTEM = (
    "You are an experienced urgent care clinician. Given the situation and "
    "your read of the patient, say your next lines to the patient in the "
    "selected register. Respond with the spoken lines only."
)
READS = [
    {"affect_observed": "frightened", "acuity": "low", "register_selected": "warm",
     "prior_relationship": "none",
     "why": "fear is disproportionate to findings; needs steadying before facts"},
    {"affect_observed": "dismissive", "acuity": "moderate", "register_selected": "firm",
     "prior_relationship": "none",
     "why": "minimizing real risk; needs the stakes stated plainly"},
    {"affect_observed": "calm", "acuity": "low", "register_selected": "brisk",
     "prior_relationship": "none",
     "why": "informed and comfortable; efficiency respects their time"},
]


def tokens(s: str) -> list[str]:
    return re.findall(r"[a-z']+", s.lower())


def ngrams(toks: list[str], n: int) -> set[tuple]:
    return {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def jaccard(a: str, b: str) -> float:
    wa, wb = set(tokens(a)), set(tokens(b))
    return len(wa & wb) / len(wa | wb) if wa | wb else 1.0


def has_loop(text: str, n: int = 6, times: int = 3) -> bool:
    toks = tokens(text)
    counts = Counter(tuple(toks[i:i + n]) for i in range(len(toks) - n + 1))
    return any(c >= times for c in counts.values())


def fabricated(prompt: str, output: str) -> int:
    p_nums = set(re.findall(r"\d+(?:\.\d+)?", prompt))
    return sum(1 for n in re.findall(r"\d+(?:\.\d+)?", output) if n not in p_nums)


def prompt_from_read(read: dict, situation: str) -> str:
    return (
        f"Situation: {situation}\n"
        f"Patient affect observed: {read['affect_observed']}\n"
        f"Acuity: {read['acuity']}\n"
        f"Prior relationship: {read['prior_relationship']}\n"
        f"Register selected: {read['register_selected']}\n"
        f"Why this register: {read['why']}\n"
        f"Say your next lines to the patient:"
    )


def train_ngrams(n: int = 8) -> set[tuple]:
    grams = set()
    for line in (DATA_DIR / "train.jsonl").read_text().splitlines():
        completion = json.loads(line)["messages"][-1]["content"]
        grams |= ngrams(tokens(completion), n)
    return grams


def make_generator(adapter_path: str | None):
    from mlx_lm import generate, load

    model, tokenizer = load(BASE_MODEL, adapter_path=adapter_path)

    def gen(user_prompt: str) -> str:
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user_prompt}]
        prompt = tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, enable_thinking=False
        )
        return generate(model, tokenizer, prompt=prompt, max_tokens=MAX_TOKENS,
                        verbose=False).strip()

    return gen


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", default=str(DATA_DIR / "lora_v2_best"))
    parser.add_argument("--heldout-situations", type=int, default=4)
    args = parser.parse_args()

    corpus_grams = train_ngrams()
    ood = json.loads(OOD_PATH.read_text())["scenarios"]
    heldout = [json.loads(l)["situation"].removeprefix("Situation: ")
               for l in (DATA_DIR / "modulation_eval.jsonl").read_text().splitlines()]
    heldout = list(dict.fromkeys(heldout))[: args.heldout_situations]

    suites = [("heldout", heldout), ("ood", [s["situation"] for s in ood])]
    stats = {}
    transcripts = []
    blind = []

    for label, adapter in [("base", None), ("adapter", args.adapter)]:
        gen = make_generator(adapter)
        for suite, situations in suites:
            key = (label, suite)
            stats[key] = {"sim": [], "mem": [], "loops": 0, "fab": 0, "n": 0}
            for situation in situations:
                outs = []
                for read in READS:
                    p = prompt_from_read(read, situation)
                    out = gen(p)
                    outs.append((read["register_selected"], out))
                    s = stats[key]
                    s["n"] += 1
                    s["fab"] += fabricated(p, out)
                    s["loops"] += has_loop(out)
                    g = ngrams(tokens(out), 8)
                    s["mem"].append(len(g & corpus_grams) / len(g) if g else 0.0)
                    blind.append({"suite": suite, "situation": situation,
                                  "register": read["register_selected"],
                                  "model": label, "output": out})
                sims = [jaccard(a[1], b[1]) for i, a in enumerate(outs) for b in outs[i + 1:]]
                stats[key]["sim"].extend(sims)
                transcripts.append(f"\n## [{label}/{suite}] {situation}")
                for reg, out in outs:
                    transcripts.append(f"\n[{reg}]\n{out}")
        del gen

    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
    lines = ["# Adapter v2 challenge eval", ""]
    lines.append(f"{'suite':<10} {'model':<8} {'modulation(sim,lower=better)':<30} "
                 f"{'mem 8-gram overlap':<20} {'loop rate':<11} {'fabricated'}")
    for suite, _ in suites:
        for label in ("base", "adapter"):
            s = stats[(label, suite)]
            lines.append(
                f"{suite:<10} {label:<8} {mean(s['sim']):<30.3f} "
                f"{mean(s['mem']):<20.3f} {s['loops']}/{s['n']:<9} {s['fab']}")
    summary = "\n".join(lines)
    print(summary)

    # blind dump: shuffled, unlabeled, key at end
    rng = random.Random(20260725)
    order = list(range(len(blind)))
    rng.shuffle(order)
    blind_lines, key_lines = ["\n\n# Blind A/B (register-fit judgment)"], ["\n\n# Blind key"]
    for i, idx in enumerate(order):
        b = blind[idx]
        blind_lines.append(f"\n### item {i:02d} [{b['suite']}/{b['register']}] {b['situation'][:80]}\n{b['output']}")
        key_lines.append(f"item {i:02d} = {b['model']}")
    out_path = DATA_DIR / "eval_v2_report.md"
    out_path.write_text(summary + "\n" + "\n".join(transcripts)
                        + "\n".join(blind_lines) + "\n".join(key_lines) + "\n")
    print(f"\nfull report + blind items: {out_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
