# Clinical Encounter Video — Annotation and Adapter Research

*Referred to throughout the decision log as the Encounter Delivery Harness.*

**The research question.** In a primary care or urgent care clinical encounter the care plan is largely
protocol, but the pratitioner delivery is highly variable. Bedside manner or Delivery involves pacing, register, stance,
word choice, each conditioned on the clinician's read of the patient in that clinical encounter. This project asks whether that dimension can be
captured, labeled reliably, and used as research dataset: whether video of real encounters can
yield **clinical practice-pattern and delivery-style labels** usable to direct with a LORA trained adapter
current and likely more capable future multimodal clinical AI models.

Current research deliverables

**1. A labeled clinical video dataset — the durable asset.** Annotated
encounter video (clinical read, selected register, the communicative move,
the observable patient response, all under a versioned schema) is scarce, and
it is useful to AI clinical research well beyond this project's own
hypothesis: communication assessment, resident education, practice-pattern
description, and any future model that must reason about *how* care is
delivered rather than only what was decided. The corpus and its schema stand
on their own regardless of what happens to the adapter.

**2. A capture-and-annotation harness, and adapters trained from it —
with limited but positive proof of concept, and under active testing.** The pipeline runs end to end and the
instrument is real, but the central claim (that a model can be conditioned to
modulate delivery register) has **not** been demonstrated. Three training
cycles on an archival proxy corpus produced a measured null, and one earlier
positive result was retracted when it failed to replicate across seeds. See
[Adapter pilot](#adapter-pilot-on-archival-film-adc-012015) for exactly what
has and has not been shown. Substantially more testing — and, the evidence
suggests, real clinical data — is required before anything here should be
relied on.

## Method, in brief

**From clip to training pair.** Each annotated clip becomes one
prompt/completion pair. The prompt carries the *clinical read* — situation,
patient affect observed, acuity, prior relationship, the selected register,
and why that register was chosen. The completion is what the clinician
actually said in that clip. Annotations come from two sources, kept
unmixable by construction: human annotation through the local UI
(`annotator/`), and machine annotation (`derive/`) that is explicitly test
scaffolding for the pathway, not a substitute for human labels (ADC-012).

**Adapter training.** Low-Rank Adaptation (LoRA; Hu et al. 2021) over
**`mlx-community/Qwen3-8B-4bit`** — Qwen3-8B, 4-bit quantized, running
locally on Apple silicon via MLX. Rank 8, scale 20, dropout 0, applied to
the top 16 layers; batch 4, Adam, `--mask-prompt` so loss falls only on the
delivery text and never on the clinical read; two-learning-rate sweep
(1e-5 / 5e-6) with the best-validation checkpoint kept. Validation is a
**fully held-out film**, never a random split, because near-duplicate clips
leak across a random boundary. LoRA is chosen deliberately: it transfers
*form*, not *content* (§1 of the plan), which is exactly the separation this
project needs — the base model must keep supplying clinical reasoning while
the adapter supplies only delivery.

**Evaluation.** The same local Qwen3-8B-4bit, with and without the adapter,
generating from identical prompts under identical sampled decoding
(temp 0.7, top-p 0.95, repetition penalty 1.15 — fixed after greedy decoding
was found to confound an entire round of results). There is no ground-truth
delivery to score against, so the battery measures the adapter *against the
untuned base model* on held-out material along four axes: **register
isolation** (vary only the register word, hold everything else fixed, scored
against a within-register resampling control — the metric that produced the
current null), **memorization** (8-gram overlap with training completions),
**degeneracy** (loop rate), and **fabrication** (numbers appearing in output
that were absent from the prompt). A blind A/B of shuffled adapter/base
outputs, key sealed until after scoring, is where physician judgment enters —
the only place a human decides whether a register actually fits.
Multi-seed replication is required before any result is recorded as a
finding; one earlier positive result did not survive it.

Full decision map: [docs/harness-action-plan.md](docs/harness-action-plan.md).

## Status: pre-Phase 0

Legal predicates (recording ownership under
the practice arrangement, privacy , IRB scope) is limited for personal use
**gate all capture work** — see plan section 2. Resolving them through an
IRB-governed academic partnership is the direction now being pursued; see
[Research direction](#research-direction-irb-governed-capture-partnership-sought)
below. Meanwhile the conditioning pathway has been tested end to end on
archival film — see [Adapter pilot](#adapter-pilot-on-archival-film-adc-012015).
What exists now are the pre-capture deliverables from plan section 9:

| Item | Where | Plan ref |
|---|---|---|
| Annotation record schema (JSON Schema 2020-12) | `schema/encounter_record.schema.json` | 4 |
| Memory-encounter test instrument | `tools/memory_annotation.html` | 9.4 |
| Corpus throughput model | `scripts/throughput_model.py` | 9.5, 8.7 |
| Rust ingest core (`harness check` / `ingest` / `validate`) | `harness/` | 3, 5 |
| Scrub-latency prototype + clip generator | `tools/scrub_prototype.html`, `scripts/make_scrub_clip.sh` | ADC-005 |
| Annotation UI (clip-review loop) | `annotator/` | 4, ADC-011 |
| Architecture decision log | `docs/ADC.md` | — |

## Run locally

The memory-encounter test (no dependencies — open in a browser):

    open tools/memory_annotation.html

Annotate 10 encounters from memory. The `schema_gap` field per record is the
output of the exercise: what mattered that the schema could not capture.
Records stay in browser localStorage; export JSON when done. De-identify
everything.

The throughput model (stdlib only):

    python3 scripts/throughput_model.py

The Rust ingest core (the never-lose-data path, ADC-004; requires a Rust
toolchain):

    cd harness
    cargo test
    cargo run --release -- check <capture-dir> --json-report <capture-dir>/capture-check.json
    cargo run --release -- ingest <capture-dir> --store <store-dir>
    cargo run --release -- validate --store <store-dir>

Check is the end-of-session capture verification (run before tearing down
the room): every audio track is fully decoded and inspected for dead
channels, clipping, wrong sample rate/bit depth, and truncation; video
containers are parsed for track inventory and duration; and all files must
agree on duration within a sync tolerance (defaults: 48 kHz, 24-bit, 1.0 s
spread; override with `--expect-sample-rate`, `--expect-bit-depth`,
`--sync-tolerance-secs`). Ingest copies each file into a content-addressed
immutable store (ADC-007: `originals/<hash-prefix>/<blake3>.<ext>`,
read-only, verified by re-hashing the stored bytes before the manifest
entry is written) and appends to `manifest.jsonl`. Validate re-hashes
everything against the manifest and reports missing, corrupted, and
orphaned files. All three exit nonzero on any failure.

The commands chain: `check --json-report` writes `capture-check.json` into
the capture directory; `ingest` finds it automatically, refuses to ingest a
failed session (override with `--allow-failed-check`), and writes each
file's check result as a provenance sidecar
(`provenance/<hash>.capture-check.json`) keyed by content hash, so
capture-quality evidence travels with the original forever.

When `ffprobe` is installed, `check` also reads each video file's start
timecode (`tmcd` track) into the report and sidecars — informational until
the real rig's timecode behavior is known — and falls back to ffprobe for
container parsing when the pure-Rust MP4 parser cannot handle a QuickTime
file.

The annotation-UI scrub-latency prototype (ADC-005 hedge):

    ./scripts/make_scrub_clip.sh scrub_test_clip.mp4 120   # needs ffmpeg
    open tools/scrub_prototype.html                        # then load the clip

Drag the scrub strip hard and run both automated tests. Decision
thresholds on p95 seek latency: under 50 ms excellent, under 100 ms
acceptable, over 150 ms means escalating to the WebCodecs path
(docs/research/annotation-ui.md). Measured 2026-07-22 in Safari/WebKit:
p95 9–10 ms, zero seek coalescing — settled.

The annotation UI (needs the venv: `python3 -m venv .venv &&
./.venv/bin/pip install -r requirements.txt`):

    ./.venv/bin/python -m annotator.main --clips <proxy-dir>

Open the printed `http://127.0.0.1:8765/?token=...` URL. The server is
loopback-only; the token gates `/api/*` (the media is PHI, the static shell
is not). Each media file in the directory is one clip in the review queue:
scrub (same controls as the prototype), mark in/out with `I`/`O`, fill the
schema form, `⌘↵` saves and advances to the next unannotated clip. Every
record is validated server-side against
`schema/encounter_record.schema.json` before it reaches SQLite; rejects
come back with field-level paths. `export JSONL` dumps the schema-pure
records.

## Headline finding so far

Under the plan's own assumption that performance-moments are 3–5% of runtime
(a handful of clips per shift), the corpus growth bottleneck is **supply**
(moment occurrence × consent rate × shifts recorded), not annotation labor:
even the aggressive scenario needs under one hour per week of annotation to
clear its full supply. Five-year totals run ~400 (conservative) to ~3,700
(aggressive) annotated clips. Consequence: consent-rate design and number of
recorded shifts dominate corpus size; annotation-UI throughput does not.
See `docs/ADC.md` (ADC-003).

## Adapter pilot on archival film (ADC-012..015)

While capture is gated, the conditioning pathway was tested end to end on a
proxy corpus: public-domain and openly licensed clinical and counseling films
(the Gloria trilogy, Beck, Meichenbaum, a motivational-interviewing session,
standardized-patient encounters, a mid-century psychiatric interview series).
Training cycles ran on 14 films (~6.8 h); the corpus has since grown to 22
(~9.4 h). Films were machine-annotated against the schema, exported to
prompt/completion pairs, and used to train LoRA adapters on a local Qwen3-8B.

**Demonstrated.** The loop closes end to end and entirely locally:
capture-validate → annotate → export → train → evaluate. Style transfer is
real and visible. No verbatim memorization survives the export fixes (0.000
8-gram overlap with training completions on held-out material). Speaker-
attributed labels eliminated the adapter's worst failure mode outright.

**Not achieved.** Register conditioning — the project's central claim — does
not appear at this corpus size. A register-isolation test (vary only the
selected register, hold situation and clinical read fixed, against a
within-register resampling control) shows the adapter's between-register
variation is indistinguishable from its own sampling noise across three
seeds, while the untuned base model does steer on the register word. An
earlier positive result was **retracted** when multi-seed testing failed to
replicate it — see the correction in ADC-014.

**What the pilot establishes about the corpus, not the instrument.** The
archival films are staged demonstrations: uniformly low acuity, no
established clinician–patient relationships, registers entangled with
individual demonstrators (nearly all "firm" delivery in the corpus comes from
two therapists), and single-track audio that turns speaker attribution into a
modeling problem instead of a recording property. Each of those is a property
of the proxy corpus. None is a property of the method.

## Research direction: IRB-governed capture partnership (sought)

The binding constraint here is not engineering. The instrument is built and
the pathway is validated; what does not exist is a lawful, consented corpus of
real encounters — and the pilot above quantifies precisely why archival
footage cannot substitute for one.

The direction being pursued is an academic research partnership: resident
family-medicine clinical encounters captured under institutional IRB
governance, with the partnering institution retaining rights to the video
database and the derived annotations for future research. This project
contributes the instrument — capture validation, the annotation UI and
schema, the derive pipeline, the export and training path, and the evaluation
battery including the register-isolation test that produced the null above.

Requirements the pilot has already established, which a capture protocol
should specify from the outset:

- **Per-speaker audio tracks** (separate lapel channels, not a room mic).
  Single-track audio caused the pilot's most damaging defect: adapters trained
  on undiarized transcripts spoke the patient's lines back. Channel = speaker
  removes it at the source instead of modeling around it (ADC-013, ADC-015).
- **Counterfactual coverage as a sampling target.** Register conditioning is
  only learnable if the corpus contains comparable clinical situations
  delivered in different registers. A multi-resident clinic supplies this
  naturally, but it should be sampled for deliberately rather than left to
  chance.
- **Separation of research use from resident evaluation.** Recording that is
  perceived as assessment changes the behavior being measured and depresses
  consent; consent design and access control should keep the two apart.

Nothing here is agreed or underway. This section documents the direction being
sought, not an existing collaboration.

## Architecture decisions (ADC-004..008, decided 2026-07-22)

- **Language**: hybrid — Rust for the never-lose-data core (ingest,
  capture-validation, fixity; one static binary), Python for derive /
  candidate detection / UI backend; coupled only by a filesystem contract.
- **Annotation UI**: build (vanilla JS + local FastAPI); both camera angles
  composed into one side-by-side all-intra H.264 proxy per clip, which makes
  multi-angle sync a non-problem.
- **Storage**: local encrypted NAS working tier + GCS (self-serve HIPAA BAA,
  CMEK, Coldline→Archive, per-object legal holds) as the immutable offsite leg.
- **Manifest** (ADC-007): content-addressed media files + append-only JSONL +
  SQLite + provenance sidecars; training formats emitted on demand, disposable.
- **Derive pipeline**: mlx-whisper + CTC alignment, pYIN/RMS prosody, affect
  as ranking signal only, MediaPipe→py-feat cascade; no pyannote.

Evidence in `docs/research/`; rationale and consequences in `docs/ADC.md`.
Decision brief (readable summary): `artifacts/decision-brief.html` — a
**snapshot dated 2026-07-22**, covering the §10 questions and the
architecture decisions through ADC-004. It predates the adapter work
entirely; for ADC-005 onward, and for everything in the Adapter pilot
section above, read `docs/ADC.md`.
