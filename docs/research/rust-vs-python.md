# Research: Rust vs Python for the harness (ADC-004 input)

Agent-researched 2026-07-22. Sources cited inline.

# Rust vs Python for a PHI Medical-Encounter Capture/Annotation Harness

**Context:** Solo physician-developer, existing Python/FastAPI/MLX stack, Apple Silicon, local-only PHI, 5-year maintenance horizon. Modules: (1) ingest, (2) derive/ML, (3) candidate detection, (4) annotation UI backend, (5) export/manifest, (6) capture-validation CLI.

---

## Q1. Supply-chain security: PyPI vs crates.io, honestly

**Incident record, 2023–2026:**

| | PyPI | crates.io |
|---|---|---|
| Volume | Hundreds of malicious typosquats removed **per month** through 2024 (Checkmarx/Phylum); a single March 2024 campaign hit 500+ typosquat fakes and forced PyPI to suspend new registrations; ongoing 2025–26 campaigns (SilentSync RAT, Shai-Hulud spillover, dYdX package compromise) | A handful of documented campaigns **per year**: `faster_log`/`async_println` (May–Sept 2025, ~8,500 downloads, secrets exfil), `evm-units` (2025, ~7,000 downloads), 5 fake time-utility crates (Feb–Mar 2026, `.env` exfil), share of the cross-ecosystem "TrapDoor" campaign |
| Scanning | PyPI has automated malware scanning + rapid takedown infrastructure | **crates.io does no automated malware scanning at upload**; detection relies on community reports and third parties (Socket, RustSec) |

Sonatype 2026 State of the Software Supply Chain: 454,600 new malicious packages in 2025 (1.233M cumulative), but **over 99% of open-source malware occurred on npm** — both PyPI and crates.io are minority targets; PyPI is a larger minority.

**Does Rust structurally reduce supply-chain risk? No.** Rust's safety is a property of the *compiled language*, not the registry. `build.rs` and proc-macros execute attacker-controlled code at build time with full user privileges — the exact analogue of Python's `setup.py` install-time execution. The measured difference in incident counts is mostly **attacker attention** (smaller ecosystem, fewer crypto-wallet targets) — real risk reduction today, not a guarantee. Memory safety and supply-chain safety are orthogonal. Caveat cutting against Rust: typical Rust projects pull deep transitive dependency trees (hundreds of crates), each with build-time code execution.

**Mitigations (both ecosystems):**
- Python: `uv` lockfiles with SHA-256 hashes by default; `pip install --require-hashes`; `pip-audit` against OSV.
- Rust: `Cargo.lock` checksums by default; `cargo-audit` (RustSec), `cargo-deny` (policy), `cargo-vet` (import Mozilla/Google human audits).
- Neither closes first-install of already-malicious code.

**Relevance to PHI:** the threat is dev-machine compromise and exfiltration at install/build time — identical shape in both ecosystems. Language choice is **not** a primary PHI control; FileVault, a no-network posture for the capture machine, minimal dependencies, and hash-locked installs are.

Sources: [Sonatype 2026](https://www.sonatype.com/state-of-the-software-supply-chain/2026/open-source-malware), [500+ PyPI typosquats](https://securityboulevard.com/2024/03/pypi-suspended-500-fakes-richixbw/), [Socket — malicious Rust crates](https://socket.dev/blog/5-malicious-rust-crates-posed-as-time-utilities-to-exfiltrate-env-files), [evm-units](https://thehackernews.com/2025/12/malicious-rust-crate-delivers-os.html), [faster_log](https://cybersecurefox.com/en/rust-crates-io-supply-chain-attack-faster-log-async-println/), [same supply-chain problem](https://llbbl.blog/2026/05/15/python-and-rust-have-the.html), [Rust supply-chain tools](https://blog.logrocket.com/comparing-rust-supply-chain-safety-tools/), [uv hash pinning](https://pydevtools.com/handbook/how-to/how-to-pin-dependencies-with-hashes-in-uv/), [Python supply-chain defense](https://bernat.tech/posts/securing-python-supply-chain/), [SilentSync](https://www.zscaler.com/blogs/security-research/malicious-pypi-packages-deliver-silentsync-rat), [Shai-Hulud](https://blog.gitguardian.com/shai-hulud-npm-pypi-supply-chain-attacks/)

---

## Q2. 5-year bit-rot

**Rust:** A documented revival of a 6-year-old Rust project took **under 10 minutes** — mechanical fixes only. Editions are an explicit stability mechanism: old editions compile forever on new compilers; `Cargo.lock` pins the graph with checksums; `rustup` installs any historical toolchain. MSRV churn only bites on dependency *upgrades* — a locked project builds as-is. A static binary from year 0 likely still runs at year 5.

**Python:** Structural disadvantage — `import` is runtime dynamic linking; the environment must be reconstructible at every future run. Concrete rot vectors: venvs break on OS/Homebrew Python upgrades (routine on macOS); stdlib removals (`distutils` gone in 3.12/PEP 632, ~20 dead-battery modules gone in 3.13/PEP 594); **old ML framework wheels are never rebuilt for new CPython versions** — reviving a 5-year-old ML environment means archiving the exact interpreter + wheels or forward-porting. This is the biggest 5-year risk in the Python option; MLX (young, pre-1.0 API habits) amplifies it.

**Mitigations for Python:** commit `uv.lock` with hashes; pin the interpreter (`uv python pin` — standalone CPython builds independent of Homebrew/OS); vendor a wheel cache on the PHI machine; treat the environment as an artifact. Closes most, not all, of the gap.

Sources: [Reviving a 6-year-old Rust project](https://blog.rng0.io/reviving-a-six-year-old-rust-project/), [PEP 632](https://peps.python.org/pep-0632/), [3.12 whatsnew](https://docs.python.org/3/whatsnew/3.12.html), [dead batteries](https://www.infoworld.com/article/2335590/what-you-need-to-know-about-pythons-dead-batteries.html)

---

## Q3. Media processing in Rust, 2026

**Verdict: the ingest/validation layer is fully buildable in Rust with mature crates**, one soft spot (timecode).

- **symphonia** (pure Rust, 100% safe, 3.2M+ downloads): FLAC and WAV/PCM both compliance-tested "Excellent"; also ISO-MP4 demuxing; decode within ±15% of FFmpeg. Covers multitrack-audio verification with no C dependency.
- **MP4/MOV demux:** `mp4` (alfg/mp4-rust) and Mozilla's `mp4parse-rust` (ships in Firefox — battle-tested).
- **ffmpeg-next** maintained (maintenance mode, FFmpeg 3.4–8.0) if full video decode verification is wanted; **gstreamer-rs** officially maintained but heavyweight for a validation CLI.
- **Soft spot — timecode:** no mature crate for QuickTime `tmcd` parsing/SMPTE math; parse the atom via `mp4parse` or shell out to `ffprobe -show_streams` (a legitimate, robust design in any language).
- Checksums: `blake3`, `sha2` best-in-class; watching via `notify`.

Sources: [Symphonia](https://github.com/pdeljanov/Symphonia), [ffmpeg-next](https://lib.rs/crates/ffmpeg-next), [mp4-rust](https://github.com/alfg/mp4-rust), [mozilla/mp4parse-rust](https://github.com/mozilla/mp4parse-rust)

---

## Q4. ML inference from Rust, 2026

**Whisper: genuinely viable from Rust** (`whisper-rs` → whisper.cpp with Metal/CoreML; candle's Whisper; `mlx-whisper-rs`).

**Everything else: possible but high-friction.** `ort` (ONNX Runtime) is the workhorse — pose works (YOLOv8-pose, RTMPose) but ort 2.0 has sat in release-candidate status for an extended period. wav2vec2 emotion: no off-the-shelf Rust path — export each HF checkpoint to ONNX and reimplement Python preprocessing by hand, a per-model porting tax repeated on every research model swap. Prosody: Praat/openSMILE/librosa have no Rust equivalents of comparable validation pedigree — matters for a clinical instrument. `mlx-rs`: unofficial, pre-1.0 — not a 5-year-stability bet.

**Verdict:** the derive layer *can* be Rust, but it converts every model swap from `pip install` + 10 lines into an ONNX-export-and-port project. For an evolving research tool, the derive layer effectively wants Python.

Sources: [whisper-rs](https://docs.rs/whisper-rs), [ort](https://github.com/pykeio/ort), [mlx-rs](https://github.com/oxiglade/mlx-rs)

---

## Q5. Hybrid patterns

1. **Python host + Rust extensions (PyO3/maturin)** — the dominant production pattern (polars, pydantic-core, tokenizers, uv) — but puts reliability-critical code *inside* the fragile Python environment. Wrong fit for a never-lose-data path.
2. **Rust core + Python ML workers** — the standard pattern when a reliability-critical core must outlive ML churn. Crash isolation is the point: a segfaulting pose model cannot corrupt the session store.
3. **PyO3 embedding Python in Rust** — inherits both ecosystems' fragility in one process. Avoid.

**Cheapest coupling for this project: no IPC at all — the filesystem contract.** The Rust ingest tool writes a content-addressed, checksummed, immutable session store + manifest; Python derive workers read from it and write derived artifacts alongside; the Rust validator re-verifies everything. Two programs, one on-disk schema, zero glue beyond JSON manifests. Pattern 2 degenerated to its simplest form; fits a batch pipeline exactly.

---

## Q6. Solo-developer velocity

- Reported ramp: ~3–6 months to feel productive in Rust vs 2–4 weeks in Python (approximate figures; direction uncontroversial).
- Rust front-loads a complexity tax. For **glue-heavy evolving research code** (modules 2–5) the tax is paid on every experiment — worst amortization. For a **small, stable-spec, correctness-critical** module (ingest + validation) the tax is paid once and the benefit lands exactly where "never lose a session" lives.
- Scope check: the ingest/validate pair is ~1–2k lines of straightforward Rust — no async, no lifetime gymnastics, excellent crates (`clap`, `blake3`, `symphonia`, `mp4parse`, `serde_json`, `notify`). A near-ideal first Rust project; a few weekends for a competent Python developer, not months.

---

## Bottom line: a specific hybrid split

| Module | Language | Why |
|---|---|---|
| 1. Ingest (watch, checksum, sync-verify, immutable store) | **Rust** (one binary with module 6) | Never-lose-data; small stable spec; mature crates; static binary immune to venv rot; compiler-enforced error handling |
| 2. Derive (whisper, prosody, emotion, pose) | **Python** (existing MLX stack) | Model churn is the workload; tooling is Python-native |
| 3. Candidate detection | **Python** | Research logic, iterated constantly |
| 4. Annotation UI backend | **Python** (FastAPI) | Velocity; not data-integrity-critical |
| 5. Export/manifest | **Python writes, Rust verifies** | Schema defined once; Rust re-validates every export |
| 6. Capture-validation CLI | **Rust** (same binary: `harness ingest` / `harness validate`) | Rock-solid is the whole point; single signed static binary |

**Interface:** content-addressed immutable session store + JSON manifests on disk. No queue, no PyO3, no embedded interpreter. Python never writes into the raw store; Rust never imports a model.

**Supporting discipline (matters more than the split for PHI):** Python — uv with committed hash-locked `uv.lock`, uv-pinned standalone interpreter, periodic `pip-audit`, vendored wheel cache. Rust — committed `Cargo.lock`, `cargo-audit` + `cargo-deny`, deliberately small dependency tree for the ingest binary. Machine — FileVault; capture machine offline or egress-filtered.

**Fallback if the Rust ramp proves unacceptable:** disciplined stdlib-only Python (hashlib, no third-party deps on the critical path) + uv hardening. Retains ~70% of the reliability benefit; cannot retain the 5-year static-binary immunity to environment rot, which is Rust's one decisive, evidence-backed advantage here.
