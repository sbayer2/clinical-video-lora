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
| Rust ingest core (`harness ingest` / `harness validate`) | `harness/` | 3, 5 |
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
    cargo run --release -- ingest <capture-dir> --store <store-dir>
    cargo run --release -- validate --store <store-dir>

Ingest copies each file into a content-addressed immutable store
(`originals/<hash-prefix>/<blake3>.<ext>`, read-only, verified by
re-hashing the stored bytes before the manifest entry is written) and
appends to `manifest.jsonl`. Validate re-hashes everything against the
manifest and reports missing, corrupted, and orphaned files with a
nonzero exit — run it before tearing down the capture room.

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
