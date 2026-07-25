"""Section-6 floor evals for the PoC adapter (ADC-013).

Three probes from the plan, applied base-vs-adapter on the same prompts:

1. Modulation (the important one): same situation, three contrasting reads.
   A working adapter shifts its output as the read shifts; a caricature
   applies one register uniformly. Metric: mean pairwise word-Jaccard
   between the three outputs of each situation — LOWER = more modulation —
   plus the outputs themselves for reading.
2. Fabrication probe: count numbers/thresholds in outputs that are absent
   from the prompt (medfit's failure mode: invented statistics).
3. Blind pairs: base and adapter outputs labeled A/B in random order for
   human judgment; the key is printed at the end.

Run:  ~/omlx/.venv/bin/python -m derive.eval_adapter [--adapter data/adapter/lora]
"""

import argparse
import json
import random
import re
from pathlib import Path

from .common import REPO_ROOT

DATA_DIR = REPO_ROOT / "data" / "adapter"
BASE_MODEL = "mlx-community/Qwen3-8B-4bit"
MAX_TOKENS = 160
SYSTEM = (
    "You are an experienced urgent care clinician. Given the situation and "
    "your read of the patient, say your next lines to the patient in the "
    "selected register. Respond with the spoken lines only."
)


def words(s: str) -> set[str]:
    return set(re.findall(r"[a-z']+", s.lower()))


def jaccard(a: str, b: str) -> float:
    wa, wb = words(a), words(b)
    return len(wa & wb) / len(wa | wb) if wa | wb else 1.0


def numbers_not_in_prompt(prompt: str, output: str) -> list[str]:
    p_nums = set(re.findall(r"\d+(?:\.\d+)?", prompt))
    return [n for n in re.findall(r"\d+(?:\.\d+)?", output) if n not in p_nums]


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
    parser.add_argument("--adapter", default=str(DATA_DIR / "lora"))
    parser.add_argument("--situations", type=int, default=4)
    args = parser.parse_args()

    probes = [json.loads(l) for l in (DATA_DIR / "modulation_eval.jsonl").read_text().splitlines()]
    by_situation: dict[str, list[dict]] = {}
    for p in probes:
        by_situation.setdefault(p["situation"], []).append(p)
    situations = list(by_situation.items())[: args.situations]

    report = {"base": {"pairwise_sim": [], "fabricated": 0},
              "adapter": {"pairwise_sim": [], "fabricated": 0}}
    transcript_lines = []
    blind_pairs = []

    for label, adapter in [("base", None), ("adapter", args.adapter)]:
        print(f"\n### generating with {label} "
              f"({'no adapter' if adapter is None else adapter}) ...")
        gen = make_generator(adapter)
        for situation, variants in situations:
            outs = []
            for v in variants:
                out = gen(v["prompt"])
                outs.append((v["read"]["register_selected"], out))
                report[label]["fabricated"] += len(numbers_not_in_prompt(v["prompt"], out))
            sims = [jaccard(a[1], b[1]) for i, a in enumerate(outs) for b in outs[i + 1:]]
            report[label]["pairwise_sim"].extend(sims)
            transcript_lines.append(f"\n## [{label}] {situation}")
            for reg, out in outs:
                transcript_lines.append(f"\n[{reg}]\n{out}")
            if label == "adapter":
                blind_pairs.append((situation, outs[0][1]))
        del gen  # free the model before loading the next

    # attach base outputs for blind pairs (first read variant of each situation)
    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
    summary = [
        "# Adapter eval summary",
        f"- modulation (mean pairwise similarity across reads; LOWER = more modulation):",
        f"    base    {mean(report['base']['pairwise_sim']):.3f}",
        f"    adapter {mean(report['adapter']['pairwise_sim']):.3f}",
        f"- fabricated numbers (absent from prompt): base {report['base']['fabricated']}, "
        f"adapter {report['adapter']['fabricated']}",
        "",
        "Read the transcripts below — metrics rank, humans judge.",
    ]
    out_path = DATA_DIR / "eval_report.md"
    out_path.write_text("\n".join(summary) + "\n" + "\n".join(transcript_lines) + "\n")
    print("\n".join(summary))
    print(f"\nfull transcripts: {out_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
