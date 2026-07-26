"""Opus as high-level curator (ADC-015, Arm B).

Unlike the windowed annotation passes (ai_annotate/local_annotate), the
curator reads each film's FULL timestamped transcript in one call and
chooses which moments carry register information, attributes speakers
from context, and writes grounded annotations with verbatim clinician
lines. A final cross-film pass reports corpus-level observations. The
brief deliberately grants latitude: the hypothesis under test is that
higher-level curation finds signal the fixed-window passes cannot.

Cost gate: running without --go prints a token/dollar estimate and
exits. No API call is made until --go is passed AND a valid key exists
in ~/.config/anthropic-derive.key (never exported to the shell).

Run:  .venv/bin/python -m derive.curate_opus [media...] [--go]
Writes scripts/out/opus_curation/<film>.curation.jsonl and
corpus_report.md (gitignored).
"""

import argparse
import json
import time
import uuid
from pathlib import Path

from .ai_annotate import transcribe_cached
from .common import REPO_ROOT, prompt_sha, sanitized_schema, sha256_file

MODEL = "claude-opus-4-8"
PRICE_IN, PRICE_OUT = 5.00, 25.00  # $/MTok
MAX_TOKENS = 16000
CURATION_DIR = REPO_ROOT / "scripts" / "out" / "opus_curation"
REGISTERS = ["brisk", "slow", "warm", "firm", "playful", "grave", "matter-of-fact"]

BRIEF = """\
You are a senior clinical-communication curator reviewing an archival
film of a clinical or therapeutic encounter for a research corpus. The
corpus trains a delivery adapter: given a clinical read (patient affect,
acuity, relationship) and a selected register, produce the clinician's
spoken lines in that register.

You see the film's full timestamped transcript. You have latitude to
curate as you judge best; the requirements are only:

1. Select the moments that genuinely carry register information — where
   the clinician's delivery mode is doing visible work. Skip narration,
   filler, and moments where delivery is incidental.
2. Attribute the speaker of every selected span from conversational
   context (these transcripts are undiarized). clinician_lines must
   contain ONLY lines you attribute to the clinician, verbatim from the
   transcript. If you cannot attribute confidently, say so in
   speaker_attribution and lower your confidence.
3. Register comes from this vocabulary: {registers}. Register means the
   clinician's whole selected delivery mode (prosody, pacing, stance,
   word choice) — never the patient's state. The patient's state goes in
   affect_observed. "why" states what in the patient's presentation
   drove the register choice, grounded in transcript evidence.
4. Seek balance across the register vocabulary where the material
   honestly allows it — but never mislabel to balance.
5. In film_observations, report anything about this film we should know
   that the fields cannot carry: counterfactual opportunities (same
   situation, different register, here or versus another film in the
   corpus), quality hazards, and especially anything unexpected — the
   research value of this pass includes what we did not think to ask.
""".format(registers=", ".join(REGISTERS))

SELECTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["selections", "film_observations"],
    "properties": {
        "film_observations": {"type": "string"},
        "selections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["t_start", "t_end", "speaker_attribution",
                             "clinician_lines", "read", "confidence", "note"],
                "properties": {
                    "t_start": {"type": "number"},
                    "t_end": {"type": "number"},
                    "speaker_attribution": {
                        "type": "string",
                        "enum": ["clinician", "mixed", "unclear"]},
                    "clinician_lines": {"type": "string"},
                    "confidence": {"type": "string",
                                   "enum": ["high", "medium", "low"]},
                    "note": {"type": "string"},
                    "read": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["acuity", "affect_observed",
                                     "prior_relationship",
                                     "register_selected", "why"],
                        "properties": {
                            "acuity": {"type": "string",
                                       "enum": ["low", "moderate", "high"]},
                            "affect_observed": {"type": "string"},
                            "prior_relationship": {
                                "type": "string",
                                "enum": ["none", "established", "unclear"]},
                            "register_selected": {"type": "string",
                                                  "enum": REGISTERS},
                            "why": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
}


def film_prompt(session_id: str, transcript: dict) -> str:
    lines = [f"Film: {session_id}. Full transcript (start-end seconds: text):"]
    lines += [f"  {s['start']:.0f}-{s['end']:.0f}: {s['text'].strip()}"
              for s in transcript["segments"]]
    return "\n".join(lines)


def estimate(media: list[Path]) -> tuple[int, int]:
    """(input_tokens, output_tokens) rough projection, transcripts cached."""
    tok_in = tok_out = 0
    for path in media:
        transcript = transcribe_cached(path)
        words = sum(len(s["text"].split()) for s in transcript["segments"])
        tok_in += int(words * 1.4) + 1800          # transcript + brief/schema
        tok_out += min(int(words * 1.6), MAX_TOKENS)  # selections echo lines
    tok_in += 6000     # synthesis pass input
    tok_out += 4000    # synthesis output
    return tok_in, tok_out


def curate(client, session_id: str, transcript: dict):
    import jsonschema

    request = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "system": BRIEF + "\nThe JSON schema:\n" + json.dumps(SELECTION_SCHEMA),
        "messages": [{"role": "user",
                      "content": film_prompt(session_id, transcript)}],
    }
    import anthropic
    try:
        response = client.messages.create(
            **request,
            output_config={"format": {
                "type": "json_schema",
                "schema": sanitized_schema(SELECTION_SCHEMA)}},
        )
    except anthropic.BadRequestError:
        response = client.messages.create(**request)

    validator = jsonschema.Draft202012Validator(SELECTION_SCHEMA)
    for attempt in range(2):
        text = next(b.text for b in response.content if b.type == "text")
        result = json.loads(text)
        errors = list(validator.iter_errors(result))
        if not errors:
            return result, response.usage
        if attempt == 1:
            raise RuntimeError(f"{session_id}: curation failed validation twice: "
                               + "; ".join(e.message for e in errors[:5]))
        response = client.messages.create(
            **{**request, "messages": request["messages"] + [
                {"role": "assistant", "content": text},
                {"role": "user", "content":
                    "That failed schema validation:\n"
                    + "\n".join(e.message for e in errors[:10])
                    + "\nEmit the corrected JSON only."}]},
        )


def synthesize(client, summaries: list[dict]) -> tuple[str, object]:
    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        system=("You curated each film below individually. Now report at "
                "corpus level, in markdown: register balance and gaps, "
                "cross-film counterfactual opportunities (same situation, "
                "different register), quality hazards for adapter training, "
                "and anything unexpected the per-film passes surfaced. "
                "Close with concrete recommendations for the export step."),
        messages=[{"role": "user", "content": json.dumps(summaries)}],
    )
    return next(b.text for b in response.content if b.type == "text"), response.usage


def usd(tok_in: int, tok_out: int) -> float:
    return tok_in / 1e6 * PRICE_IN + tok_out / 1e6 * PRICE_OUT


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("media", type=Path, nargs="*",
                        default=sorted((REPO_ROOT / "media" / "archive").glob("*.mp4")))
    parser.add_argument("--go", action="store_true",
                        help="actually call the API (default: estimate and exit)")
    args = parser.parse_args()

    tok_in, tok_out = estimate(args.media)
    print(f"{len(args.media)} films; projected ~{tok_in/1000:.0f}k in / "
          f"~{tok_out/1000:.0f}k out on {MODEL}"
          f" = ~${usd(tok_in, tok_out):.2f}"
          f" (ceiling ${usd(tok_in, len(args.media) * MAX_TOKENS + 4000):.2f}"
          " if every film maxes out)")
    if not args.go:
        print("Dry run. Re-run with --go and a valid key in "
              "~/.config/anthropic-derive.key to execute.")
        return

    import anthropic
    key_file = Path.home() / ".config" / "anthropic-derive.key"
    api_key = key_file.read_text().strip() if key_file.exists() else None
    client = anthropic.Anthropic(api_key=api_key)  # None -> env/default chain
    CURATION_DIR.mkdir(parents=True, exist_ok=True)
    print(f"model: {MODEL}, prompt: {prompt_sha()}")

    spent_in = spent_out = 0
    summaries = []
    for path in args.media:
        session_id = path.stem
        transcript = transcribe_cached(path)
        result, usage = curate(client, session_id, transcript)
        spent_in += usage.input_tokens
        spent_out += usage.output_tokens
        out_path = CURATION_DIR / f"{session_id}.curation.jsonl"
        with out_path.open("w") as f:
            for sel in result["selections"]:
                f.write(json.dumps({
                    "annotator": "machine-opus-curator",
                    "model": MODEL,
                    "record_id": str(uuid.uuid4()),
                    "annotated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                  time.gmtime()),
                    "session_id": session_id,
                    "source_sha256": sha256_file(path),
                    **sel,
                }) + "\n")
        counts = {}
        for sel in result["selections"]:
            reg = sel["read"]["register_selected"]
            counts[reg] = counts.get(reg, 0) + 1
        summaries.append({"film": session_id, "register_counts": counts,
                          "n_selections": len(result["selections"]),
                          "observations": result["film_observations"]})
        print(f"{session_id}: {len(result['selections'])} selections {counts}; "
              f"running cost ${usd(spent_in, spent_out):.2f}")

    report, usage = synthesize(client, summaries)
    spent_in += usage.input_tokens
    spent_out += usage.output_tokens
    (CURATION_DIR / "corpus_report.md").write_text(report)
    print(f"\ncorpus report: {CURATION_DIR / 'corpus_report.md'}")
    print(f"total: {spent_in} in / {spent_out} out = ${usd(spent_in, spent_out):.2f}")


if __name__ == "__main__":
    main()
