# Encounter Delivery Harness

A capture-and-annotation harness for clinical encounter delivery. The premise:
in urgent care the plan is largely protocol, but *how it lands* is not — and
that delivery skill, conditioned on the clinician's read of the patient, is
discarded by every ambient-scribe pipeline. This project captures it at high
fidelity, annotates it in an architecture-neutral schema, and aims to produce
a style adapter for a future multimodal model.

Full decision map: [docs/harness-action-plan.md](docs/harness-action-plan.md).

## Status: pre-Phase 0

Nothing records anything yet. Legal predicates (recording ownership under
the practice arrangement, malpractice-carrier position, IRB scope) are
unresolved and
**gate all capture work** — see plan section 2. What exists now are the
pre-capture deliverables from plan section 9:

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
immutable store (`originals/<hash-prefix>/<blake3>.<ext>`, read-only,
verified by re-hashing the stored bytes before the manifest entry is
written) and appends to `manifest.jsonl`. Validate re-hashes everything
against the manifest and reports missing, corrupted, and orphaned files.
All three exit nonzero on any failure.

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

## Architecture decisions (ADC-004..008, decided 2026-07-22)

- **Language**: hybrid — Rust for the never-lose-data core (ingest,
  capture-validation, fixity; one static binary), Python for derive /
  candidate detection / UI backend; coupled only by a filesystem contract.
- **Annotation UI**: build (vanilla JS + local FastAPI); both camera angles
  composed into one side-by-side all-intra H.264 proxy per clip, which makes
  multi-angle sync a non-problem.
- **Storage**: local encrypted NAS working tier + GCS (self-serve HIPAA BAA,
  CMEK, Coldline→Archive, per-object legal holds) as the immutable offsite leg.
- **Manifest**: content-addressed media files + append-only JSONL + SQLite +
  provenance sidecars; training formats emitted on demand, disposable.
- **Derive pipeline**: mlx-whisper + CTC alignment, pYIN/RMS prosody, affect
  as ranking signal only, MediaPipe→py-feat cascade; no pyannote.

Evidence in `docs/research/`; rationale and consequences in `docs/ADC.md`.
Decision brief (readable summary): `artifacts/decision-brief.html`.
