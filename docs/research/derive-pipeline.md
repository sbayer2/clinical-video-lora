# Research: derive + candidate-detection pipeline on Apple Silicon (plan section 10, item 3)

Agent-researched 2026-07-22. Sources cited inline. License notes flagged because commercial use is a live possibility. Rust-callability noted per tool.

## 1. Transcription + word-level timestamps

- **mlx-whisper is the throughput winner on M-series**: large-v3-turbo, 13.1s vs whisper.cpp 26.7s on the same long file — 2.0x faster, both far faster than realtime ([billmill.org benchmark, Jan 2026](https://notes.billmill.org/dev_blog/2026/01/updated_my_mlx_whisper_vs._whisper.cpp_benchmark.html)).
- **Word timestamps are the weak point of native Whisper**: DTW/cross-attention timestamps desync 100–400ms with occasional gross failures ([whisper.cpp #375](https://github.com/ggml-org/whisper.cpp/issues/375), [CrisperWhisper](https://arxiv.org/pdf/2408.16589)). For pause/rate prosody features keyed to word boundaries, use **external CTC forced alignment** (torchaudio `forced_align` — explicitly preserved despite torchaudio maintenance mode ([pytorch/audio #3902](https://github.com/pytorch/audio/issues/3902)) — or WhisperX's alignment stage, BSD-2 ([m-bain/whisperX](https://github.com/m-bain/whisperX))). MFA 3.0 remains phone-level SOTA (<15ms) if ever needed ([Interspeech 2026 state-of-alignment](https://arxiv.org/abs/2606.18466)).
- **pyannote is NOT needed**: separate lapel track per speaker means channel = speaker. Diarization reduces to per-track VAD + cross-track relative-energy attribution for bleed/overlap. (For the record: pyannote code MIT, models gated-but-free incl. commercial; skip anyway.)
- **whisper-rs** (whisper.cpp bindings, Metal): v0.16.0, actively maintained — a genuinely production-usable Rust path if wanted.
- License watch: MMS aligner checkpoint is CC-BY-NC — verify variant before commercial use; torchaudio bundled `MMS_FA` released for alignment use.

## 2. Prosodic feature extraction

- **openSMILE: LICENSE PROBLEM.** Free for research only; no commercial product use without a paid audEERING license ([LICENSE](https://github.com/audeering/opensmile/blob/master/LICENSE)). eGeMAPS (the clinical-speech standard 88-feature set) is defined by openSMILE configs.
- **Praat/parselmouth: GPL-3** — fine internal, viral if a proprietary binary is distributed. Gold-standard F0/jitter/shimmer and the de-facto reference for speech-rate/pause scripts.
- **Pragmatic split that avoids both**: pause + speech rate from VAD + forced alignment; F0 from pYIN (librosa, ISC) + RMS energy. Covers change-point features. CREPE/torchcrepe overkill for clean 48/24 lapel audio.
- **Rust-native**: [`pyin` crate](https://crates.io/crates/pyin) (librosa-faithful port), `pitch-detection` (MIT/Apache); energy/pause/spectral flux trivial with `rustfft`. Jitter/shimmer/HNR is where Rust coverage is thin — if voice quality earns its place, that's the Python/Praat dependency.

## 3. Change-point detection: state of practice

- What ships is **heuristic-first**: VAD pause structure + speaker-turn boundaries dominate segmentation of natural conversation; pitch adds more for read speech (Shriberg et al.; [PLOS ONE prosodic boundaries in spontaneous speech](https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0250969)).
- **ruptures** (BSD-2) PELT over a ~1Hz multivariate prosody stream (per-speaker F0 median/slope, energy, rate, pause fraction, arousal) is the standard offline tool; runs in seconds per session; penalty tuning suits the recall bias. Offline batch beats Bayesian-online here (post-session derive, both directions of context available).
- Rust: [`changepoint` crate](https://redpoll.ai/blog/changepoint/) has BOCPD; no mature PELT crate (portable in a few hundred lines if wanted).
- **Recommended detector stack (union, recall-biased)**: (a) turn-structure heuristics — long pauses, latency shifts, turn-length asymmetry, interruption density; (b) CPD over prosody+affect streams; (c) transcript keyword/semantic triggers per segment class. Snap boundaries to VAD silences, pad generously.

## 4. Speech emotion recognition — honest reliability

- **audeering wav2vec2 A/D/V** (MSP-Podcast, spontaneous speech — good domain match; ONNX published incl. tiny Wav2Small): **CC-BY-NC-SA — non-commercial.** The biggest license landmine in the pipeline; fine for research phase, must swap or license before productizing.
- **emotion2vec+/SenseVoice**: custom FunASR model licenses — must be read before commercial use. SenseVoice's laughter/crying event detection is a useful bonus channel.
- **Reliability on spontaneous speech**: arousal from audio works; **valence from audio alone is weak** — transformer fine-tunes reach valence CCC ≈ 0.638 on MSP-Podcast but partly by reading linguistic content, and cross-corpus valence transfer stays weak ([Wagner et al., TPAMI 2023](https://arxiv.org/abs/2203.07378)); in-the-wild 3-class UA ~55–62% ([EMOVOME](https://arxiv.org/pdf/2403.02167)).
- **Design consequence**: SER is a *change-point feature and ranking signal*, never a clip classifier. Arousal trajectory is trustworthy enough to flag de-escalation candidates (patient high→low while clinician stays low); triangulate valence with transcript sentiment. SER proposes, the human disposes.
- Rust: all three export to ONNX → `ort`; pre/post-processing trivial. Most Rust-friendly ML component.

## 5. Video side

- **MediaPipe** (Apache-2.0, active): Face Landmarker (478 landmarks + 52 blendshapes ≈ expression proxies), Pose Landmarker; fully offline; real-time on M-series at 1080p. No dedicated gaze task — iris + head pose gives a usable proxy.
- **OpenFace 3.0** ([CMU](https://github.com/CMU-MultiComp-Lab/OpenFace-3.0), FG 2025): AUs + gaze + emotion, but **custom CMU non-commercial license, no sublicensing** — ruled out for product without a deal.
- **py-feat** (MIT, MPS support since v0.7): the default AU toolchain despite being slower/research-grade. Python-only.
- **Cascade is what keeps video tractable**: MediaPipe landmarks+blendshapes at ~10fps continuously (cheap); py-feat AUs at 3–5fps only inside audio-flagged windows; process the better-visibility angle per speaker. Full-rate AUs on every frame of both angles would take hours per session.

## 6. Wall-clock estimate (15-min encounter, M3/M4 Pro–Max class, 64GB)

Estimates extrapolated from cited benchmarks, not measured:

| Stage | Est. wall-clock |
|---|---|
| VAD (Silero, both tracks) | < 0.5 min |
| ASR large-v3-turbo both tracks (mlx-whisper) | 0.5–1 min |
| CTC forced alignment (MPS) | 1.5–3 min |
| Prosody (pYIN + energy + pause/rate) | 1–3 min |
| SER (A/D/V, 2s hop) | 2–6 min |
| Change-point + scoring | < 0.5 min |
| MediaPipe landmarks @10fps, both angles | 3–6 min |
| py-feat AUs @3–5fps, flagged windows only | 5–15 min |
| **Total audio-only** | **~6–14 min (sub-realtime)** |
| **Total with video** | **~15–35 min (≈1–2.5x session length)** |

First engineering task once real data exists: a one-session smoke benchmark on the target machine.

## 7. Rust ML runtime maturity

`ort` production-grade (CoreML/CPU EPs); candle mature pure-Rust with Whisper/wav2vec2-class implementations; mlx-rs active but unofficial/pre-1.0 — not a clinical-product foundation. The derive layer is not strictly Python-only, but best-in-class forced-alignment ergonomics, eGeMAPS-grade voice quality, and the entire video AU stack remain Python/C++ islands.

## Recommended pipeline

Rust harness (capture, orchestration, candidate assembly) + contained Python derive worker, coupled by a file contract (per-track feature files + proposed spans) so components can migrate individually:

1. Per track: Silero VAD → speech spans, pause inventory; channel = speaker; overlap via VAD intersection + relative energy. No pyannote.
2. ASR: mlx-whisper large-v3-turbo (Python worker); whisper-rs/Metal if Rust-first (~2x slower, still ≫ realtime). 48k→16k mono downmix per track.
3. Word alignment: CTC forced alignment of the Whisper transcript (torchaudio/WhisperX). Don't trust Whisper DTW timestamps.
4. Prosody: pYIN F0 + RMS + pause stats + rate from alignment. Add parselmouth jitter/shimmer only if review shows voice quality earns its place (mind GPL).
5. Affect: audeering A/D/V ONNX on 2s hops (research phase; swap before commercialization), emotion2vec+ as cross-check (license review). Arousal reliable, valence advisory.
6. Video cascade: MediaPipe continuously at ~10fps; py-feat AUs only in flagged windows.
7. Candidates: union of heuristics + PELT/BOCPD + transcript triggers; score, snap to silences, pad generously; emit review queue.

**License risk register (commercial)**: openSMILE (no commercial use) · audeering SER weights (CC-BY-NC-SA) · OpenFace 3.0 (CMU non-commercial) · Praat/parselmouth + aubio (GPL-3) · emotion2vec/SenseVoice weights (custom — review) · MMS aligner (CC-BY-NC — verify). **Clean-for-commercial core**: whisper.cpp/mlx-whisper (MIT), WhisperX (BSD-2), Silero VAD (MIT), MediaPipe (Apache-2.0), py-feat (MIT), ruptures (BSD-2), librosa (ISC), ort/candle/whisper-rs (MIT/Apache).

**Open uncertainties**: exact FunASR model-license terms (read, don't summarize); MMS-aligner license variant; wall-clock table unmeasured; MediaPipe C++ desktop Tasks completeness.
