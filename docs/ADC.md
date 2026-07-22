# Architecture Decision Cycle log

Numbered in order. Close entries by editing them, never by deleting.
Source plan: [harness-action-plan.md](harness-action-plan.md).

---

## ADC-001 — Formalize the annotation record schema (done)

**Context.** Plan section 4 sketches the record in JSONC. It needs to be a
validatable artifact before the 9.4 memory test can mean anything.

**Decision.** JSON Schema 2020-12 at `schema/encounter_record.schema.json`,
`schema_version` 0.1.0. Additions relative to the plan's sketch, each flagged
here so they can be reverted:

- `capture: recording | memory` — one schema serves both the 9.4 memory test
  and real recordings; `clip` is required only for recordings.
- `record_id` (uuid) separate from `session_id` — a session yields many records.
- `context` free-text field — memory records have no tape to consult; a
  de-identified one-line setting is needed to interpret the read.
- `schema_gap` free-text field — "what mattered that the fields could not
  capture." For memory records this is the entire point of the exercise
  (plan 9.4); retained for recordings as a running schema-adequacy signal.
- `self_rating.worked` kept binary per the plan. Open question: a graded
  scale would carry more contrast signal; revisit after the memory test.

**Consequences.** Schema changes bump `schema_version`; the annotation UI and
export manifest validate against it. Any field added later must justify why
it wasn't caught by `schema_gap` entries first.

---

## ADC-002 — Memory test before any capture tooling (done)

**Context.** Plan 9.4: the cheapest test of the core premise is whether the
schema captures what mattered in 10 well-remembered encounters. This precedes
hardware, ingest, everything.

**Decision.** Built `tools/memory_annotation.html` — a zero-dependency,
fully local, single-file instrument. localStorage persistence, JSON export
conforming to schema 0.1.0, `schema_gap` visually emphasized as the exercise's
output. De-identification warning inline.

**Consequences.** The 10 exported records are the acceptance test for
ADC-001. If `schema_gap` entries cluster (e.g. everything missing is about
timing/pacing, or about multi-turn arcs longer than one clip), the schema
gets revised before Phase 1 hardware is specified.

---

## ADC-003 — Throughput model result: supply-bound, not annotation-bound (done)

**Context.** Plan 8.7 names annotation labor as the rate limit and most
likely cause of project death. Plan 9.5 requires modeling the growth curve
before deciding whether the terminal number justifies the build.

**Decision.** `scripts/throughput_model.py` models weekly annotated clips as
min(supply, capacity), where supply = shifts × notable-clips-per-shift ×
consent-rate and capacity includes triage of detector false positives
(triage_time / precision per kept clip).

**Result.** Under the plan's own 4.1 assumption (performance moments are a
handful per shift), supply binds in every scenario, not capacity:

| scenario | annotated/wk | hrs/wk needed | 5-year total |
|---|---|---|---|
| conservative | 1.8 | 0.2 | ~414 |
| moderate | 6.3 | 0.4 | ~1,449 |
| aggressive | 16.0 | 0.7 | ~3,680 |

This **corrects plan 8.7**: annotation labor is not the bottleneck at these
supply levels; even aggressive supply needs <1 hr/week to annotate fully.
The levers that actually move the 5-year total are consent rate, shifts
recorded, and how liberally "notable" is defined. The LIMA-style ~1k
threshold is reached by the moderate scenario; the plan's 2,500 figure
requires the aggressive one.

**Consequences.** (a) Consent instrument design (plan 0.5) is promoted from
compliance task to corpus-size-determining task. (b) Annotation-UI throughput
optimization is deprioritized; correctness and low friction still matter,
speed does not. (c) If "notable" is broadened to grow supply, selection
discipline (plan 4.1) is the thing protecting corpus quality — revisit
detector precision assumptions then. (d) Rerun with real numbers after the
first recorded month.

---

## ADC-004 — Implementation language: hybrid, Rust core + Python derive (done)

**Context.** Plan section 5 assumed Python. User direction 2026-07-22:
consider Rust for the entire harness (security/compatibility concerns).
Research ([research/rust-vs-python.md](research/rust-vs-python.md)) found:
(a) the supply-chain security argument for Rust is weak — crates.io does no
automated malware scanning and `build.rs` executes arbitrary code at build
time exactly like `setup.py`; the incident-count gap is attacker attention,
not structure; (b) the *bit-rot* argument is strong and evidence-backed — a
6-year-old Rust project revived in minutes vs Python's venv/wheel/interpreter
rot, which MLX amplifies; (c) the derive-layer ML ecosystem (whisper aside)
is effectively Python/C++ — full Rust would tax every model swap with an
ONNX-export-and-port project; (d) ingest/validation is ~1–2k lines of easy
Rust with mature crates (symphonia, mp4parse, blake3, notify).

**Decision (user-confirmed 2026-07-22).** Hybrid, split on the
reliability/velocity boundary, coupled only through the filesystem:

| Module | Language |
|---|---|
| Ingest + capture-validation + fixity verify | **Rust** — one static binary (`harness ingest` / `harness validate`) |
| Derive (whisper, prosody, affect, pose) | **Python** (MLX stack) |
| Candidate detection | **Python** |
| Annotation UI backend | **Python** (FastAPI) |
| Export | **Python writes, Rust verifies** |

Interface: content-addressed immutable session store + JSON manifests on
disk. No PyO3, no queue, no embedded interpreter. Python never writes into
the raw store; Rust never imports a model.

**Consequences.** (a) Language is not treated as a PHI control — the durable
controls are FileVault, egress-filtered capture machine, hash-locked installs
(committed `uv.lock` with pinned standalone interpreter; committed
`Cargo.lock` + cargo-audit/cargo-deny, deliberately small tree for the ingest
binary). (b) The Rust binary owns everything that touches raw PHI bytes on
the never-lose-data path. (c) Fallback if the Rust ramp stalls: stdlib-only
disciplined Python for ingest — retains most reliability, loses the 5-year
static-binary immunity.

---

## ADC-005 — Annotation UI: build; composed-proxy pipeline; web + FastAPI (done)

**Context.** Plan section 10 framed this as Electron/Tauri vs web+local
server with multi-angle scrub latency deciding. Research
([research/annotation-ui.md](research/annotation-ui.md)) dissolved the
framing: ProRes/DNxHR decodes in **no** webview (Chromium, WKWebView, or
browser) and has no WebCodecs codec string, so proxy transcode is
unconditional; and two `<video>` elements can never be frame-locked during
scrubbing — but composing both angles into **one side-by-side all-intra
H.264 proxy per clip** (ffmpeg `hstack`, keyint 1, VideoToolbox) makes sync
perfect by construction and scrubbing single-stream, which all candidates
handle in hardware. Survey of existing tools (ELAN, BORIS, Datavyu, Label
Studio, CVAT, VIA, FiftyOne): none combines 2-angle sync + waveform + rich
schema forms + 2-min/clip keyboard cadence; the schema form is the product.

**Decision.** Build, not adopt. Pipeline: composed side-by-side all-intra
proxies; frame accuracy via mid-frame `currentTime` seeks +
`requestVideoFrameCallback` verification; waveform peaks precomputed at
transcode time. Framework: **web (vanilla JS) + local FastAPI server bound to
127.0.0.1 with a session token** — consistent with ADC-004's
minimal-Rust/velocity directive and the existing stack. Tauri (which the
UI research favored on PHI posture and escape hatches) is retained as a
packaging escalation, not the starting point: the frontend is identical
vanilla JS either way, so the switch stays cheap.

**Consequences / cheap hedges before committing.** (a) Half-day ELAN trial
with ~5 real clips — the only tool that could make building unnecessary.
(b) Week-1 scrub-latency prototype: jog wheel over a 2-min side-by-side
all-intra clip in the browser. (c) One-file empirical check that ProRes-MOV
really does not play in the target webview. All three predate any real UI
code. Localhost-port PHI surface is accepted and mitigated (loopback bind,
token, no LAN exposure); revisit via Tauri if it starts to itch.

**Update (hedge b built).** `tools/scrub_prototype.html` +
`scripts/make_scrub_clip.sh`: generates a 2-min 3840x1080 side-by-side
all-intra H.264 clip (hardware-encoded, hue-shifted angle B, built-in
clock for eyeball sync verification) and measures seek latency in the
browser — per-seek latency from `currentTime` assignment to `seeked`
(issued-vs-completed exposes coalescing), presented-frame readout via
`requestVideoFrameCallback`, mid-frame seek targeting, a 100-seek random
test and a 5 s seek-storm torture test, with verdicts at the research
thresholds (p95 <50 ms excellent / <100 ms acceptable / >150 ms escalate
to WebCodecs). Human measurement pass still pending.

**Measured 2026-07-22 (hedge b closed).** Random-seek test, 100/100
seeks completed, on real long-GOP camera footage (a harder decode case
than the all-intra proxy) in what appears to be Safari/WebKit — the
engine Tauri would embed, and the riskier of the two per the research:
mean 5.8 ms, **p95 9.0 ms**, max 15.0 ms, ~172 effective seeks/s.
The 50 ms "excellent" threshold is beaten 5×. Verdict: webview
scrubbing is settled; the WebCodecs escalation path is retired unless
real proxies someday regress; the web + vanilla JS + local-server UI
decision stands with margin. All-intra proxies remain specified
because they guarantee the property rather than relying on fast
hardware long-GOP decode. Torture-drag run, same session: 301 seeks
issued / 300 completed — effectively zero coalescing — at 60.0
effective seeks/s (display-rate-limited, not decode-limited), mean
6.1 ms, p95 10.0 ms, max 13.0 ms under continuous storm. Hedge fully
closed. Residual caveat, logged by the operator: the test clip was
audio-light; audio-track seek-resync overhead is expected to be small
against a 15× margin, and annotation proxies carry a single stereo
track (multitrack audio is served as precomputed waveform peaks, not
live decode). Spot-check with an audio-heavy clip when convenient.

---

## ADC-006 — Storage tiering: local NAS working tier + GCS archival tier (done)

**Context.** Plan section 10: GCS + local NVMe vs fully local given PHI.
Research ([research/storage-manifest.md](research/storage-manifest.md))
resolved the key blocker: **Google's HIPAA BAA is self-serve, electronic,
and has no minimum spend** — a solo practice can execute it in minutes, and
GCS/KMS/Logging are covered services. HHS guidance is explicit that
client-side encryption does *not* remove the BAA requirement, so every cloud
leg needs one regardless.

**Decision.** Both tiers, not either: (1) local encrypted NAS
(Btrfs/ZFS, data checksums enabled, monthly scrubs, RAID-Z2/SHR-2) as the
capture/working tier where selective retention culls the 100–200 GB/hr
firehose; (2) GCS under the self-serve BAA as the durable immutable tier —
CMEK, Coldline with lifecycle transition to Archive (~$1.2–4.4/TB/mo),
**per-object legal holds rather than a locked bucket retention policy** (a
locked policy is irrevocable and would prevent honoring consent revocation;
CMEK gives crypto-shred), Data Access audit logs exported to a locked log
bucket. Optional third leg: Backblaze B2 with BAA for manifest + highest-value
masters. Heavy re-derivation runs on in-region GCP compute (free egress from
GCS) — pulling a 50 TB corpus down costs ~$8.5k in retrieval+egress.

**Consequences.** The offsite leg is the cloud, not rotated drives — manual
rotation is the leg most likely to fail under solo operation. Upload
bandwidth, not storage cost, is the real constraint; selective retention
happens before anything leaves the building. Useful datum for the plan-0.3
carrier conversation: PMC10917358 found encounter video recording was not
associated with increased malpractice claims. Gated behind Phase 0 —
nothing is stored until plan section 2 resolves.

---

## ADC-007 — Corpus manifest: content-addressed files + JSONL + SQLite (done)

**Context.** Plan section 10: WebDataset, Arrow, or custom, judged on
surviving ~5 years of unknown downstream encoders.

**Decision.** The boring option, deliberately
([research/storage-manifest.md](research/storage-manifest.md)): originals as
plain native media files named by BLAKE3 content hash
(`originals/<prefix>/<hash>.mov`); append-only JSONL manifest as ground
truth (hash, timestamps, encounter id, consent reference, device, codec
params, size, duration); SQLite for annotations and queries, rebuildable
from JSONL; JSON provenance sidecars per derived artifact (source hashes,
tool version = git tag, exact parameters); BagIt-style `manifest-sha256.txt`
per acquisition batch for standard fixity tooling. Training-stack formats
(WebDataset/HF/Lance/Parquet) are emitted on demand as **disposable derived
artifacts** — plan section 5's design rule, confirmed by the evidence:
at thousands of clips columnar formats buy nothing measurable, and the
5-year stability records of Lance (3 format iterations in ~2 years) and HF
datasets (v4.0 broke loading scripts and caches) argue against them as
storage. JSON and SQLite have perfect stability records; SQLite is a Library
of Congress preservation format.

**Consequences.** Fixity discipline: monthly filesystem scrub + quarterly
harness re-hash sweep + cloud-side hash checks without egress, every check
logged (the log is chain-of-custody evidence). The Rust core (ADC-004) owns
manifest verification; this stack is also the one with the best Rust library
story (`object_store`, `rusqlite`, `serde_json`, `blake3`).

---

## ADC-008 — Derive pipeline components (provisional)

**Context.** Plan section 10: which prosody/affect models run on 64 GB
Apple silicon. Research: [research/derive-pipeline.md](research/derive-pipeline.md).

**Decision (provisional until benchmarked on real capture).**
- **No pyannote** — separate lapel track per speaker makes channel =
  speaker; diarization reduces to per-track Silero VAD + relative-energy
  overlap attribution.
- ASR: mlx-whisper large-v3-turbo (2x whisper.cpp on M-series, both ≫
  realtime). Word timing from **CTC forced alignment**, not Whisper's DTW
  timestamps (100–400 ms desync).
- Prosody: pYIN F0 + RMS energy + pause/rate stats from alignment —
  deliberately avoids openSMILE (research-only license) and Praat (GPL)
  unless jitter/shimmer earns its place later.
- Affect: audeering wav2vec2 arousal/valence/dominance ONNX — **as a
  change-point feature and ranking signal only, never a clip classifier**
  (valence from audio alone is weak on spontaneous speech; arousal is the
  trustworthy channel). License landmine: CC-BY-NC-SA — must be swapped or
  licensed before any commercial use.
- Video cascade: MediaPipe landmarks/blendshapes ~10 fps continuously
  (Apache-2.0); py-feat AUs (MIT) only inside audio-flagged windows.
  OpenFace 3.0 excluded (CMU non-commercial license).
- Candidates: union of turn/pause heuristics + ruptures PELT over ~1 Hz
  per-speaker feature streams + transcript keyword triggers; snap to VAD
  silences, pad generously (recall bias — the human filters).
- Estimated derive pass for a 15-min encounter: ~6–14 min audio-only,
  ~15–35 min with video (extrapolated, unmeasured).

**Open.** First engineering task on real data: one-session smoke benchmark.
License risk register lives in the research doc; revisit before any
commercial deployment.

---

## ADC-009 — Capture-check media verification scope (done)

**Context.** Plan section 3 ("build now"): a capture-validation script that
verifies sync, channel separation, levels, and file integrity at end of
session, before the room is torn down.

**Decision.** Implemented as `harness check <capture-dir>` in the Rust core
(`harness/src/media.rs`). Scope, deliberately asymmetric per ADC-008:

- **Audio is decode-verified sample by sample** (symphonia, pure Rust — no
  C dependency near raw PHI): expected sample rate and bit depth (defaults
  48 kHz / 24-bit per plan section 3), per-channel RMS/peak, dead-channel
  detection (< -60 dBFS RMS), clipping detection (≥4 full-scale samples),
  and truncation (decoded frames vs header-declared frames). Audio is the
  prosody payload; it gets the deepest check.
- **Video is container-verified only** (mp4 crate): parseability, track
  inventory (a camera file with no video track fails), dimensions, nonzero
  duration. ProRes essence decode is out of scope — it belongs to the
  derive layer.
- **Sync is checked as duration agreement** across all media files in the
  session (default tolerance 1.0 s). True timecode (`tmcd` atom) alignment
  is deferred until the capture rig exists and its actual timecode
  behavior is known (the Rust ecosystem has no mature tmcd crate;
  `ffprobe` fallback is the likely route — research/rust-vs-python.md Q3).

  **Update:** the ffprobe route is now built as an *informational* layer:
  when ffprobe is installed, `check` reads each video file's start
  timecode into the report/sidecars (verified against
  ffmpeg-generated ProRes MOVs with tmcd tracks, including inter-camera
  start offsets), and container parsing falls back to ffprobe when the
  pure-Rust mp4 crate cannot handle a QuickTime file. What stays deferred
  until the rig exists: making timecode a *checked* invariant (agreement
  window, drift, LTC-on-audio decoding if the rig free-runs) — that
  policy needs real rig behavior to be designed against.
- An unreadable media file is a failed check on that file, not an abort of
  the session check; an empty capture directory never passes.

**Consequences.** `check` (pre-ingest, room still standing) and `validate`
(post-ingest fixity) stay separate commands with separate failure meanings.
Ten integration tests cover the failure taxonomy; synthetic WAVs are
generated in-test, so the suite needs no fixtures. Revisit sync checking
when real rig footage exists (ADC-008's smoke benchmark).

---

## ADC-010 — Check results become provenance sidecars at ingest (done)

**Context.** ADC-007 mandates JSON provenance sidecars per artifact. The
capture check (ADC-009) produced only human-readable output, so its
evidence evaporated after the terminal scrolled.

**Decision.** `harness check --json-report` writes a machine-readable
report; the conventional location is `capture-check.json` inside the
capture directory. `harness ingest` then:

- refuses to ingest a session whose report is not clean
  (`--allow-failed-check` overrides — the failure evidence is then
  preserved rather than discarded, since failed captures are still data);
- never stores the report file itself as an original;
- writes `provenance/<hash>.capture-check.json` per matched file — keyed
  by BLAKE3 content hash, not filename, so provenance survives renames and
  applies to deduplicated re-ingests exactly once;
- counts and warns about files the report does not cover (added or changed
  after the check ran).

Sidecars carry the per-file result plus the report-level context it was
produced under (expectations, sync verdict, tool version, timestamp).
Issue records are stored as raw JSON so readers never need to track the
`Issue` enum. `provenance/` lives beside `originals/`, which keeps the
fixity orphan scan (`validate`) scoped to originals only.

**Consequences.** Capture-quality evidence travels with the original for
the corpus's lifetime — the annotation UI and export layers can surface
"this session had a dead channel" without re-deriving it. The gate makes
`check` mandatory in practice, which is what plan section 3 intended.
Three new integration tests cover the flow (17 total).

---

## ADC-011 — Annotation UI clip-review loop (done, v0)

**Context.** ADC-005 settled the architecture (web + vanilla JS + local
FastAPI, composed proxies) and the scrub measurement retired the latency
risk. This is the first real slice of plan section 5 module 4: the loop the
annotator lives in.

**Decision.** `annotator/` — FastAPI on 127.0.0.1 with a per-run token
gating `/api/*` only (media is PHI; the static shell is not), vanilla JS
frontend. Structural choices:

- **Queue model**: a directory of proxy files, one clip each — the
  candidate-detection pipeline will later emit exactly this shape, so the
  UI is decoupled from how clips get proposed.
- **Schema stays the single source of truth**: every record is validated
  server-side against `schema/encounter_record.schema.json` before
  insertion (422s carry field-level paths back to the form); the client
  pulls enums and required-ness from `/api/schema` rather than duplicating
  them.
- **SQLite stores schema-pure JSON**: the record column validates against
  the schema exactly; UI-side facts (which proxy file) live in side
  columns, never inside the record — so `additionalProperties: false`
  keeps meaning something.
- **Keyboard loop**: prototype scrub controls carried over; `I`/`O` mark
  in/out and become `clip.t_start`/`t_end`; save-and-next auto-advances to
  the first unannotated clip. Multiple records per clip are allowed
  (a clip can contain more than one move).
- Range serving verified (206) — seeking works; media never leaves
  loopback.

**Consequences / open.** Waveform lane (precomputed peaks at transcode
time) not yet present; candidate-queue and capture-check sidecar surfacing
("this session had a dead channel") pending derive-layer integration;
outcome-linkage is a separate deferred pass per the plan; no record
edit/delete in v0 — annotate again and reconcile at export. Six server
tests cover the gate, queue, Range, validation, and traversal safety.

**First real-use findings (2026-07-22, archive.org clinical films).**
(a) Defect found and fixed: with no in/out marks set, save silently
recorded the whole file as the clip — the exact opposite of the
selection discipline the harness exists for. Now: no marks blocks save;
`I` alone yields a single-frame clip; `I`+`O` a segment. (b) First
schema_gap datum: what mattered was a *patient-state observable*
("appetite of the patient receiving the food") — a behavioral/
physiological read the schema's affect-only `read` section cannot hold.
Watch for this class recurring in the 9.4 memory test before adding a
field. (c) Annotating third-party footage bends the `why` field toward
describing the observed clinician rather than the annotator's own read —
the schema presumes annotator = clinician; fine for tool trials, worth
remembering for any Tier-3 coaching reuse.
