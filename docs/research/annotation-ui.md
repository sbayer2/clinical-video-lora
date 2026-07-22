# Research: annotation UI architecture (ADC-005 input)

Agent-researched 2026-07-22. Sources cited inline; verify flagged items before committing.

# Annotation Harness Technical Research: Video Decode, Multi-Angle Sync, Tool Survey, Build Recommendation

Research date: 2026-07-22. All findings from web sources cited inline. Confidence flags noted where sources are secondary.

---

## Q1. Video decode reality: ProRes in webviews, proxy workflow, frame-accurate seeking APIs

### ProRes in Chromium/Electron: No

- Chromium's `<video>` decode path is a curated FFmpeg build supporting only web streaming codecs (H.264, HEVC on supported hardware, VP8/VP9, AV1). ProRes is not in the supported set and never has been ([chromium.org/audio-video](https://www.chromium.org/audio-video/)).
- Electron ships Chromium's proprietary-codec-enabled build (H.264/AAC work), but that does not extend to ProRes ([electron/electron#633](https://github.com/electron/electron/issues/633)).
- DNxHR: same story — not a web codec, not decodable in any browser `<video>` element.

### ProRes in WKWebView (Tauri on macOS): Also no, despite native OS support

- macOS itself decodes ProRes natively (AVFoundation/VideoToolbox, hardware ProRes engines on M1 Pro and later — [WWDC20 session 10090](https://developer.apple.com/videos/play/wwdc2020/10090/)), but WebKit's media stack applies a codec allowlist for web content. Safari/WKWebView play `.mov` containers only when the inside codec is a web codec (H.264, HEVC, AAC…). ProRes-in-MOV does not play in the `<video>` element ([totalmedia.ai on Safari format support](https://www.totalmedia.ai/en/resources/blog/why-safari-struggles-with-video-formats), [Apple forums — WKWebView codec support is poorly documented, H.264 the only firmly documented baseline](https://developer.apple.com/forums/thread/785680)). Confidence: high for "don't count on it"; Apple publishes no authoritative list, so verify empirically with one test file, but no source anywhere reports ProRes `<video>` playback working.
- WebCodecs cannot rescue this: the WebCodecs codec registry covers avc/hevc/vp8/vp9/av1/etc. — no ProRes codec string exists, so even manual decode via `VideoDecoder` is impossible in any webview.

**Consequence: if the UI lives in any webview (Electron, Tauri, or plain browser), proxy transcode is mandatory. Native decode of ProRes originals is only possible via AVFoundation or mpv/FFmpeg outside the webview.**

### Proxy workflow (standard practice, directly applicable)

- Editing-industry standard: transcode camera originals to an edit-friendly proxy. For browser playback that means H.264 or HEVC in MP4 ([Frame.io proxy workflow guide](https://blog.frame.io/2024/07/29/updated-guide-premiere-pro-proxies-and-proxy-workflows/)).
- **All-intra / keyframe-dense encoding is the load-bearing detail for scrubbing.** Long-GOP H.264 must decode from the previous keyframe to display an arbitrary frame; with default GOP sizes scrubbing stutters. A directly relevant data point from an Adobe forum test: H.264 proxies at default keyframe interval 72 scrubbed "stuttery and laggy"; at **keyframe interval 1 (all-intra), scrubbing matched ProRes 422 Proxy** ([Adobe community thread](https://community.adobe.com/questions-729/are-h-264-proxy-files-really-that-bad-anymore-1397483), [intraframe vs long-GOP discussion](https://community.adobe.com/t5/premiere-pro-discussions/the-difference-between-intraframe-like-prores-and-long-gop-like-h-264-codecs/m-p/14574978)).
- Practical encode for this project: `ffmpeg -i in.mov -c:v h264_videotoolbox -g 1 -b:v 30M` (or `libx264 -g 1 -crf 18`) per clip. All-intra 1080p H.264 at visually-fine quality runs roughly 30–80 Mbps — clips are ~2 min, storage is cheap, and VideoToolbox encodes faster than realtime on Apple Silicon. HEVC all-intra is also viable (Chrome 107+ and Electron ≥ v33 have hardware HEVC via VideoToolbox on macOS, including Rext 4:2:2 on Apple Silicon; Safari/WKWebView has HEVC natively — [StaZhu HEVC guide](https://github.com/StaZhu/enable-chromium-hevc-hardware-decoding)). H.264 all-intra is the safest lowest-friction choice; both engines decode it in hardware.

### Frame-accuracy APIs

- **`currentTime` seek**: setting `currentTime` performs a *precise* seek in both engines (decode from prior keyframe to the exact time); with all-intra media that is one frame of decode work. But the HTML spec does not guarantee frame accuracy — time-to-frame rounding can land you one frame off ([w3c/media-and-entertainment#4, the canonical thread on this](https://github.com/w3c/media-and-entertainment/issues/4), [old but still-instructive WebKit bug 52697 "Frame accurate seeking isn't always accurate"](https://bugs.webkit.org/show_bug.cgi?id=52697)). Standard mitigation: seek to `(frameIndex + 0.5) / fps` (mid-frame target) so rounding cannot cross a frame boundary.
- **`fastSeek()`** (WebKit/Firefox): keyframe-tolerance seek, speed over precision — with all-intra media every frame is a keyframe so fastSeek and precise seek converge ([MDN fastSeek](https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/fastSeek)). Chromium doesn't implement it; not needed.
- **`requestVideoFrameCallback` (rVFC)**: fires per presented frame with `mediaTime`, which identifies the exactly-displayed frame — this is how you *verify* which frame is on screen and display a correct frame counter/timecode. Supported in Chromium and Safari/WebKit (not Firefox, irrelevant here). Best-effort (main thread vs compositor), so treat as telemetry, not a clock ([web.dev rVFC article](https://web.dev/articles/requestvideoframecallback-rvfc), [MDN](https://developer.mozilla.org/en-US/docs/Web/API/HTMLVideoElement/requestVideoFrameCallback)).
- **WebCodecs**: full manual control — demux (mp4box.js), feed `VideoDecoder`, paint exact frames to canvas. This is the gold standard for frame-accurate browser video ([w3c discussion](https://github.com/w3c/media-and-entertainment/issues/4)). Support: Chromium since 94 (complete); Safari 16.4–18.x video-only (`VideoDecoder` yes, `AudioDecoder` no), **full WebCodecs from Safari 26** ([webkit STP release notes](https://webkit.org/blog/13575/release-notes-for-safari-technology-preview-157/), [testmu compat summary](https://www.testmuai.com/learning-hub/webcodecs-browser-support/)). On current macOS both engines have what's needed (video decode is the part that matters; audio can go through WebAudio).
- **Engine differences that matter**: Chromium's `currentTime` updates coarsely during playback (~4 Hz) — use rVFC `mediaTime` for display, not `currentTime` polling. WebKit historically had the seek-tolerance behaviors above. With all-intra media + mid-frame seek targets + rVFC verification, both engines deliver reliable frame-accurate stepping; this combination is the established recipe.

---

## Q2. Multi-angle sync in a webview, and native alternatives

### Two `<video>` elements: workable but never truly frame-locked

- Known technique: master/slave — one element is the clock; a `requestAnimationFrame` (or rVFC) loop measures drift and nudges the slave via small `currentTime` corrections or `playbackRate` micro-adjustment ([Bocoup: Synchronizing playback of two videos](https://www.bocoup.com/blog/html5-video-synchronizing-playback-of-two-videos), [bkeepers gist](https://gist.github.com/bkeepers/4979576)). Libraries: timingsrc/MediaSync from the W3C timing community ([media sync for timing object](https://www.w3.org/community/webtiming/2015/10/15/media-sync-for-timing-object)).
- Limits: each element has an independent decode pipeline; there is no cross-element frame lock primitive. Achievable in practice is roughly within-1-frame alignment during steady playback with periodic resync, but corrections cause visible micro-stutters, and during *scrubbing* (rapid seek storms) the two elements complete seeks at different times — the angles visibly tear apart, exactly during the interaction this project cares most about ([Bocoup](https://www.bocoup.com/blog/html5-video-synchronizing-playback-of-two-videos), [W3C media production workshop — media element accuracy](https://www.w3.org/2021/03/media-production-workshop/talks/slides/sacha-guddoy-media-element-accuracy.pdf), [frame-accurate sync TPAC talk](https://w3.org/2019/Talks/TPAC/frame-accurate-sync)).
- Paused frame-stepping of two elements (seek both, wait for both `seeked` events) *is* reliable — the pain is live scrubbing/jog.

**The decisive workaround: compose both angles into one file.** Since a proxy transcode step is mandatory anyway (Q1), render the two angles side-by-side (or PiP) into a *single* 3840×1080 all-intra proxy per clip (`ffmpeg hstack`). One `<video>`, one decode clock, sync is perfect by construction, scrub latency is single-stream, and multitrack audio is muxed once with known offsets. This is exactly the workaround the CVAT community converged on for multi-camera work ([cvat-ai/cvat#4915](https://github.com/cvat-ai/cvat/issues/4915)). For a fixed 2-angle rig with time-aligned recordings, it eliminates the entire sync problem class. Cost: fixed layout (no per-angle zoom without cropping tricks), and angles must already be time-aligned at transcode time (fine — alignment is a one-time per-recording step, and the harness controls the transcode).

Two-element sync remains a fallback if independent per-angle interaction (e.g., solo-zoom one angle) proves essential.

### Native alternatives

- **AVFoundation**: the "correct" macOS answer for genuine multi-stream lock — multiple `AVPlayerItem`s slaved to one `AVSynchronizedLayer`/host clock; plays ProRes originals directly, `AVPlayerItemVideoOutput` gives exact frames. Rust access via `objc2`/`objc2-av-foundation` bindings is possible but rough; a small Swift helper (separate NSView layered under/next to the webview, controlled over IPC) is the pragmatic shape. Significant native-UI plumbing for a solo dev.
- **libmpv**: mpv decodes ProRes/DNxHR natively (full FFmpeg), has exact seeks (`hr-seek`), frame-step/back-step. Embedding in Tauri exists today: [tauri-plugin-libmpv](https://github.com/nini22p/tauri-plugin-libmpv) (render-API embed) and [tauri-plugin-mpv](https://github.com/nini22p/tauri-plugin-mpv) (JSON-IPC to an mpv window), plus community discussion of the texture-sharing approach ([tauri discussion #6343](https://github.com/tauri-apps/tauri/discussions/6343), [#15171](https://github.com/orgs/tauri-apps/discussions/15171)). Caveats: Rust libmpv bindings are under-maintained (libmpv2-rs), and *two synchronized mpv instances* re-imports the sync problem — you'd still want the composed single-stream file, at which point mpv's advantage (native ProRes) is mostly moot since composition implies transcode anyway. mpv's real remaining advantage: could play a composed **ProRes proxy** (better scrub decode than H.264, no generation loss) — but macOS hardware decodes all-intra H.264 effortlessly at 1080p, so this is marginal.

---

## Q3. Existing open-source tool survey

| Tool | Multi-angle sync | Waveform | Custom schema forms | Keyboard-driven | Local-only | Extensibility | License |
|---|---|---|---|---|---|---|---|
| **ELAN** (MPI, actively maintained through 2025) | Yes — up to 4 videos, time-offset alignment, sync mode; audio comes from first video only; sync quality serviceable but not frame-locked across players | Yes — native; since 6.1 extracts waveform from video's audio track, `.wav` preferred for accuracy/perf ([release history](https://archive.mpi.nl/tla/elan/rel-history-25)) | Partial — tiers + controlled vocabularies (typed hierarchical annotations), not arbitrary structured forms | Strong — CV entries bound to keys, segmentation press-hold-release mode | Yes, fully offline desktop | Java codebase; extensible but heavyweight to fork | GPLv3 |
| **BORIS** ([paper](https://besjournals.onlinelibrary.wiley.com/doi/10.1111/2041-210X.12584), [github](https://github.com/olivierfriard/BORIS)) | Partial — multiple simultaneous players per observation; sync via offsets, not frame-locked | Yes — audio waveform/spectrogram panel | Weak — ethogram (event/state + modifiers), not rich per-clip forms | Strong — single-key event coding is its core design | Yes | Python/Qt — very forkable for a Python dev | GPLv3 |
| **Datavyu** ([features](https://datavyu.org/features.html), [github](https://github.com/databrary/datavyu)) | Yes — multiple time-locked streams, its headline feature; custom FFmpeg/AVFoundation JNI player ([ffmpegplugin](https://github.com/databrary/datavyu-ffmpegplugin)) | Limited | Yes-ish — spreadsheet columns with typed codes; Ruby scripting API | Strong — keyboard "jog shuttle" numpad workflow | Yes | Java + Ruby scripting; aging codebase, development slow | GPLv3 |
| **Label Studio** ([video templates](https://labelstud.io/templates/video_timeline_segmentation.html), [1.20 release](https://labelstud.io/blog/label-studio-1-20-0-spectrograms-time-series-sync-playground-2-0-and-jsonl-support/)) | Partial — `sync` aligns video+audio+timeseries; two *videos* synced not first-class | Yes — waveform + spectrogram (1.20+) | **Excellent** — XML-config labeling forms | Moderate — hotkeys exist, not built for 2-min/clip cadence | Yes — self-host local server | Very high | Apache-2.0 (OSS core) |
| **CVAT** ([multi-cam issue #4915](https://github.com/cvat-ai/cvat/issues/4915)) | **No** — community workaround is tiling into one video | No | Limited — geared to boxes/tracks | Moderate | Yes — Docker, heavy | High but large codebase | MIT |
| **VIA** ([VGG site](https://www.robots.ox.ac.uk/~vgg/software/via/)) | No | No | Basic — attributes on temporal segments | Moderate | Yes — single self-contained HTML file | Trivially hackable (vanilla JS) — good code to *read* | BSD-2 |
| **FiftyOne** ([docs](https://docs.voxel51.com/user_guide/annotation.html)) | No — curation/QA app | No | Via plugins | Weak for this workload | Yes | High (Python + JS plugins) | Apache-2.0 |

**Survey verdict:** The behavioral-coding lineage (ELAN, Datavyu, BORIS) has multi-video sync and keyboard coding but weak structured-form schemas and dated Java/Qt stacks; the CV lineage (Label Studio, CVAT, VIA, FiftyOne) has good forms/extensibility but no real multi-angle sync and no frame-accurate scrub culture. Nothing hits all of: 2-angle frame-locked scrub + waveform + rich schema form + 2-min/clip keyboard cadence. Closest adaptable options: **ELAN** (worth a 1-day trial before building) and **BORIS**. Also note **EASELAN** (2025, [arxiv 2510.15767](https://arxiv.org/html/2510.15767v1)) — evidence that research groups extend ELAN rather than replace it, and of where its edges are.

---

## Q4. Recommendation

### Build, don't adopt — with one cheap hedge

The schema form is the product (structured medical-encounter fields, ~2 min/clip cadence); every surveyed tool compromises exactly there or on multi-angle scrub. A solo dev writing a vanilla-JS single-page UI against local files is a small build. Hedge: spend half a day loading 5 real clips into ELAN (2 videos + wav, CV tiers mapped to your schema) to confirm the build is justified — it's the only tool that could plausibly make building unnecessary.

### The pipeline decision that outranks the framework decision

1. **Proxies are mandatory in any webview** — ProRes/DNxHR will not decode in Chromium or WKWebView, and WebCodecs has no ProRes codec string. Originals stay archived; the UI plays proxies.
2. **Compose the two angles into one side-by-side all-intra H.264 file per clip** (ffmpeg `hstack`, `-g 1`, VideoToolbox encode). Makes multi-angle sync a non-problem (one decode clock), scrubbing single-stream, and neutralizes the deciding factor — multi-angle scrub latency becomes single-video scrub latency, which both engines handle in hardware at 2×1080p-wide all-intra.
3. Frame accuracy recipe: all-intra media + seek to mid-frame timestamps via `currentTime` + `requestVideoFrameCallback.mediaTime` to verify/display the exact frame. WebCodecs canvas painting is the escalation path.
4. Waveform: precompute peak data per audio track at transcode time (ffmpeg/audiowaveform → JSON peaks), render with canvas or wavesurfer.js (BSD-3). Do not decode 48 kHz multitrack in the UI thread per clip load.

### Framework: Tauri v2

All three options can hit the latency target given the pipeline above, so secondary factors decide — and they favor Tauri:

- **Decode parity**: WKWebView uses the same VideoToolbox hardware decode as Safari. The Tauri-specific risk — serving large video to the webview — is solved via custom protocol with Range-request support ([tauri#4133](https://github.com/tauri-apps/tauri/issues/4133), [wry custom protocols](https://deepwiki.com/tauri-apps/wry/4.1-custom-protocols)). Implement Range semantics from day one; naive whole-file reads are the documented footgun.
- **Fits preferences**: vanilla JS frontend is Tauri's happy path; Rust side (ffmpeg subprocess orchestration, SQLite, file protocol) is small, well-trodden Rust.
- **PHI**: single signed .app, no listening sockets (custom protocol, not localhost HTTP), no Chromium auto-update infra, ~10 MB footprint.
- **Escape hatches if webview scrubbing disappoints** (measure week 1: jog wheel over a 2-min side-by-side all-intra clip): (a) WebCodecs + canvas, (b) [tauri-plugin-libmpv](https://github.com/nini22p/tauri-plugin-libmpv) (also plays ProRes originals directly), (c) Swift/AVFoundation helper view. Electron has (a) only.

**Why not Electron**: no decode advantage after proxies, ~150 MB runtime, no native escape hatch, forecloses the Rust goal. Choose only if week-1 testing showed WKWebView materially worse at seek storms *and* WebCodecs didn't close the gap — no evidence found predicts that.

**Why not web + local Python/FastAPI server**: strongest option to stay in Python — but a listening localhost port with PHI (mitigable, still a real surface), browser chrome fighting a keyboard-driven UI, no native escape hatch. A Rust axum + browser variant has the same shape and keeps the Rust goal.

### Deciding technical facts, restated

1. ProRes decodes in **no** webview and has no WebCodecs codec string → proxy transcode is unconditional for any web-tech UI.
2. All-intra H.264 proxies scrub as well as ProRes proxy in editing software (keyint-1 test) and every frame is a keyframe → `currentTime` seeks are one-frame decodes in both engines.
3. Two `<video>` elements cannot be frame-locked during scrubbing — but a composed side-by-side single file makes the question moot, and the mandatory transcode makes composition free.
4. With sync eliminated and decode in hardware on both engines, scrub latency no longer discriminates Tauri vs Electron; footprint, PHI posture, Rust preference, and escape hatches make Tauri the pick.

### Open items to verify

(a) Empirically confirm ProRes-MOV non-playback in a WKWebView with one test file (5-minute check — Apple documents nothing authoritative); (b) week-1 scrub-latency prototype before committing to the webview path; (c) half-day ELAN trial before committing to building at all.
