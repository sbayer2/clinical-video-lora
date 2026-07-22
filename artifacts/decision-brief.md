# Encounter Delivery Harness — Decision Brief

2026-07-22 · Answers to action-plan §10, results of §9.4–9.5 prep, and two corrections to the plan.
Full evidence: `docs/research/`. Decisions logged: `docs/ADC.md`.

## The five §10 questions, answered

| Question | Answer | ADC |
|---|---|---|
| Annotation UI: Electron/Tauri vs web+local server? | **Dissolved by the pipeline**: ProRes decodes in no webview, so proxies are unconditional; composing both angles into one side-by-side all-intra H.264 file per clip makes multi-angle sync perfect by construction and scrub latency single-stream. Framework then barely matters → **build (not adopt), vanilla JS + FastAPI on 127.0.0.1**; Tauri kept as packaging escalation. Hedges first: half-day ELAN trial, week-1 scrub prototype. | 005 |
| Storage: GCS + local NVMe, or fully local given PHI? | **Both.** Google's HIPAA BAA is self-serve with no minimum spend — the assumed blocker doesn't exist. Local encrypted NAS as working tier (selective retention happens here); GCS Coldline→Archive (~$1.2–4.4/TB/mo) with CMEK + per-object legal holds (not locked retention — irrevocable WORM would prevent honoring consent revocation) as the immutable offsite leg. Client-side encryption does **not** remove the BAA requirement (HHS explicit). | 006 |
| Which prosody/affect models run on 64 GB Apple silicon? | All of them, sub-realtime for audio (~6–14 min per 15-min encounter; ~15–35 min with video cascade). mlx-whisper turbo (2× whisper.cpp); CTC forced alignment (not Whisper's DTW timestamps); pYIN + RMS + pause/rate (avoids openSMILE research-only license and Praat GPL); **skip pyannote entirely** — per-speaker lapel tracks make channel = speaker. SER: arousal is trustworthy, valence is not → affect is a ranking signal, never a classifier. MediaPipe continuously, py-feat AUs only in audio-flagged windows. | 008 |
| Manifest format surviving 5 years — WebDataset, Arrow, custom? | **The boring one**: content-addressed native media files (BLAKE3 names) + append-only JSONL manifest + SQLite for annotations + JSON provenance sidecars + BagIt-style batch checksums. At thousands of clips columnar buys nothing; Lance had 3 format iterations in ~2 years, HF datasets v4 broke its own caches; JSON/SQLite have perfect records. Training formats are emitted on demand and treated as disposable — the plan's own design rule, confirmed. | 007 |
| Can Tier 3 (coaching) ship first on a small corpus? | **Yes.** Lyssn.io built a real business on models trained from ~341–1,553 expert-coded sessions — your 500–2,500 clip range is the historically demonstrated scale, and modern base models lower it (at ~500 clips, retrieval + many-shot ICL works with no training run). Zero-shot LLMs demonstrably underperform on fine-grained communication coding — the corpus is the moat. Regulatory: clinician-facing retrospective education tools are explicitly FDA non-devices. **No Lyssn-for-medical-encounters exists; that's the open lane.** Risk: ambient scribes own the audio pipe. | — |

## Implementation language (user-decided, evidence-aligned)

Hybrid, split on the reliability/velocity boundary (ADC-004): **Rust** for the never-lose-data core — ingest, capture-validation, fixity verification, one static binary — because the evidence-backed Rust win is 5-year bit-rot immunity (a 6-year-old Rust project revives in minutes; a 2026 ML venv likely won't). **Python** for derive, candidate detection, UI backend — the ML ecosystem lives there and full Rust would tax every model swap with an ONNX port. Coupling: filesystem contract only; Python never writes into the raw store, Rust never imports a model.

Notable negative finding: **the supply-chain security argument for Rust is mostly a myth** — crates.io does no automated malware scanning and `build.rs` executes arbitrary code at build time exactly like `setup.py`. The real PHI controls are FileVault, an egress-filtered capture machine, and hash-locked installs in both ecosystems.

## Throughput model (§9.5) — and a correction to §8.7

| scenario | annotated/wk | hrs/wk needed | 5-yr total |
|---|---|---|---|
| conservative | 1.8 | 0.2 | ~414 |
| moderate | 6.3 | 0.4 | ~1,449 |
| aggressive | 16.0 | 0.7 | ~3,680 |

**§8.7 named annotation labor the likely cause of death. Under the plan's own §4.1 assumption, it isn't — supply is.** Even the aggressive scenario needs under one hour/week of annotation. The levers that move the 5-year total are consent rate, shifts recorded, and the definition of "notable." Consequence: the consent instrument (§0.5) is corpus-size-determining, not a compliance formality; annotation-UI speed is deprioritized in favor of correctness and low friction. The moderate scenario clears the LIMA-style ~1k threshold — which, per the Tier-3 research, is enough to build on.

## License landmines found (before they're load-bearing)

openSMILE (research-only), audeering SER weights (CC-BY-NC-SA), OpenFace 3.0 (CMU non-commercial, no sublicense), Praat/parselmouth (GPL-3 if distributed), emotion2vec/SenseVoice (custom terms — read before use). Clean core exists for every stage: whisper/mlx-whisper (MIT), WhisperX (BSD-2), Silero VAD (MIT), MediaPipe (Apache-2.0), py-feat (MIT), ruptures (BSD-2).

## Useful datum for the carrier conversation (§0.3)

PMC10917358: video recording of encounters was not associated with increased malpractice claims; no paid claims 2000–2017 involved recording physicians. Bring it.

## What exists in the repo now

Schema 0.1.0 (`schema/`), memory-test instrument (`tools/memory_annotation.html`), throughput model (`scripts/`), five research reports (`docs/research/`), ADC-001..008 (`docs/ADC.md`).

## Sequence from here

1. **Clinician-owner**: counsel on recording ownership (§0.1) and malpractice carrier (§0.3) — still blocking everything downstream.
2. **Clinician-owner**: 10 memory encounters in the instrument; the `schema_gap` fields are the schema's acceptance test.
3. **Engineering, not gated**: Rust core skeleton (`harness ingest`/`validate` against synthetic media), ELAN half-day trial, browser scrub prototype, one-session derive benchmark on synthetic/consented-self recordings.
4. **Strategic, worth deciding early**: whether Tier 3 becomes the explicit near-term target — it changes annotation priorities (cross-walk a subsample to OS-12/VR-CoDES so the corpus is credible beyond one expert's style).
