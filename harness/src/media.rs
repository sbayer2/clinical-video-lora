//! Media verification for the end-of-session capture check (plan section 3):
//! every audio track fully decodes at the expected format, no channel is dead
//! or clipped, video containers parse with sane durations, and all files in a
//! session agree on duration to within a sync tolerance. Silent capture
//! failures discovered a week later are how corpora die; this runs before the
//! room is torn down.
//!
//! Scope note (ADC-008): audio is decode-verified sample by sample because it
//! is the prosody payload. Video is container-verified only — ProRes essence
//! decode is out of scope here and belongs to the derive layer's tooling.

use std::fmt;
use std::fs::File;
use std::io::BufReader;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use symphonia::core::audio::SampleBuffer;
use symphonia::core::codecs::DecoderOptions;
use symphonia::core::errors::Error as SymError;
use symphonia::core::formats::FormatOptions;
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::MetadataOptions;
use symphonia::core::probe::Hint;
use walkdir::WalkDir;

/// Capture-rig expectations from plan section 3, overridable on the CLI.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Expectations {
    pub sample_rate: u32,
    pub bits_per_sample: u32,
    pub sync_tolerance_secs: f64,
}

impl Default for Expectations {
    fn default() -> Self {
        Expectations {
            sample_rate: 48_000,
            bits_per_sample: 24,
            sync_tolerance_secs: 1.0,
        }
    }
}

const CLIP_LEVEL: f32 = 0.999;
const CLIP_MIN_SAMPLES: u64 = 4;
const SILENCE_RMS_DBFS: f64 = -60.0;

#[derive(Debug, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Issue {
    Decode { message: String },
    UnexpectedSampleRate { found: u32, expected: u32 },
    LowBitDepth { found: u32, expected: u32 },
    SilentChannel { channel: usize, rms_dbfs: f64 },
    Clipping { channel: usize, samples: u64 },
    Truncated { declared_frames: u64, decoded_frames: u64 },
    NoVideoTrack,
    ZeroDuration,
}

impl fmt::Display for Issue {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Issue::Decode { message } => write!(f, "decode failure: {message}"),
            Issue::UnexpectedSampleRate { found, expected } => {
                write!(f, "sample rate {found} Hz, expected {expected} Hz")
            }
            Issue::LowBitDepth { found, expected } => {
                write!(f, "bit depth {found}, expected at least {expected}")
            }
            Issue::SilentChannel { channel, rms_dbfs } => {
                write!(
                    f,
                    "channel {channel} is silent ({rms_dbfs:.1} dBFS RMS) — dead or disconnected mic?"
                )
            }
            Issue::Clipping { channel, samples } => {
                write!(f, "channel {channel} clips ({samples} full-scale samples)")
            }
            Issue::Truncated { declared_frames, decoded_frames } => {
                write!(
                    f,
                    "decoded {decoded_frames} frames but header declares {declared_frames} — truncated file?"
                )
            }
            Issue::NoVideoTrack => write!(f, "container has no video track"),
            Issue::ZeroDuration => write!(f, "container reports zero duration"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MediaKind {
    Audio,
    Video,
}

#[derive(Debug)]
pub struct MediaInfo {
    pub path: PathBuf,
    /// Path relative to the checked directory (portable across machines).
    pub rel_path: String,
    /// BLAKE3 of the file contents — the join key to the ingest manifest.
    pub hash: Option<String>,
    pub kind: MediaKind,
    pub duration_secs: Option<f64>,
    /// Start timecode (e.g. "01:02:03:04") read via ffprobe, if the file
    /// carries one and ffprobe is installed. Informational until the real
    /// rig's timecode behavior is known (ADC-009).
    pub start_timecode: Option<String>,
    /// Human summary, e.g. "48000 Hz, 2 ch, 24-bit, peak -6.0 dBFS".
    pub detail: String,
    pub issues: Vec<Issue>,
}

/// Fully decode one audio file and check it against expectations.
pub fn probe_audio(path: &Path, expect: &Expectations) -> Result<MediaInfo> {
    let file = File::open(path).with_context(|| format!("cannot open {}", path.display()))?;
    let mss = MediaSourceStream::new(Box::new(file), Default::default());
    let mut hint = Hint::new();
    if let Some(ext) = path.extension() {
        hint.with_extension(&ext.to_string_lossy());
    }
    let probed = symphonia::default::get_probe()
        .format(&hint, mss, &FormatOptions::default(), &MetadataOptions::default())
        .with_context(|| format!("unrecognized audio container: {}", path.display()))?;
    let mut format = probed.format;
    let track = format
        .default_track()
        .with_context(|| format!("no decodable track in {}", path.display()))?;
    let track_id = track.id;
    let params = track.codec_params.clone();

    let sample_rate = params
        .sample_rate
        .with_context(|| format!("no sample rate declared in {}", path.display()))?;
    let channels = params
        .channels
        .map(|c| c.count())
        .with_context(|| format!("no channel layout declared in {}", path.display()))?;
    let declared_frames = params.n_frames;

    let mut issues = Vec::new();
    if sample_rate != expect.sample_rate {
        issues.push(Issue::UnexpectedSampleRate { found: sample_rate, expected: expect.sample_rate });
    }
    if let Some(bits) = params.bits_per_sample {
        if bits < expect.bits_per_sample {
            issues.push(Issue::LowBitDepth { found: bits, expected: expect.bits_per_sample });
        }
    }

    // Decode everything, accumulating per-channel peak, energy, and clip counts.
    let mut decoder = symphonia::default::get_codecs()
        .make(&params, &DecoderOptions::default())
        .with_context(|| format!("no decoder for {}", path.display()))?;
    let mut peak = vec![0f32; channels];
    let mut sum_sq = vec![0f64; channels];
    let mut clipped = vec![0u64; channels];
    let mut frames: u64 = 0;
    let mut sample_buf: Option<SampleBuffer<f32>> = None;

    loop {
        let packet = match format.next_packet() {
            Ok(packet) => packet,
            Err(SymError::IoError(e)) if e.kind() == std::io::ErrorKind::UnexpectedEof => break,
            Err(SymError::ResetRequired) => {
                issues.push(Issue::Decode { message: "unexpected mid-stream reset".to_string() });
                break;
            }
            Err(e) => {
                issues.push(Issue::Decode { message: e.to_string() });
                break;
            }
        };
        if packet.track_id() != track_id {
            continue;
        }
        let decoded = match decoder.decode(&packet) {
            Ok(decoded) => decoded,
            Err(e) => {
                issues.push(Issue::Decode { message: e.to_string() });
                break;
            }
        };
        let buf = sample_buf.get_or_insert_with(|| {
            SampleBuffer::<f32>::new(decoded.capacity() as u64, *decoded.spec())
        });
        buf.copy_interleaved_ref(decoded);
        let samples = buf.samples();
        frames += (samples.len() / channels) as u64;
        for (i, &s) in samples.iter().enumerate() {
            let ch = i % channels;
            let a = s.abs();
            if a > peak[ch] {
                peak[ch] = a;
            }
            if a >= CLIP_LEVEL {
                clipped[ch] += 1;
            }
            sum_sq[ch] += f64::from(s) * f64::from(s);
        }
    }

    if let Some(declared) = declared_frames {
        if frames < declared {
            issues.push(Issue::Truncated { declared_frames: declared, decoded_frames: frames });
        }
    }
    for ch in 0..channels {
        let rms_dbfs = if frames > 0 {
            20.0 * (sum_sq[ch] / frames as f64).sqrt().max(1e-12).log10()
        } else {
            f64::NEG_INFINITY
        };
        if rms_dbfs < SILENCE_RMS_DBFS {
            issues.push(Issue::SilentChannel { channel: ch, rms_dbfs });
        }
        if clipped[ch] >= CLIP_MIN_SAMPLES {
            issues.push(Issue::Clipping { channel: ch, samples: clipped[ch] });
        }
    }

    let overall_peak = peak.iter().cloned().fold(0f32, f32::max);
    let peak_dbfs = 20.0 * f64::from(overall_peak.max(1e-9)).log10();
    let bits = params
        .bits_per_sample
        .map(|b| format!("{b}-bit"))
        .unwrap_or_else(|| "unknown depth".to_string());
    Ok(MediaInfo {
        path: path.to_path_buf(),
        rel_path: path.file_name().unwrap_or_default().to_string_lossy().into_owned(),
        hash: None,
        kind: MediaKind::Audio,
        duration_secs: Some(frames as f64 / f64::from(sample_rate)),
        start_timecode: None,
        detail: format!("{sample_rate} Hz, {channels} ch, {bits}, peak {peak_dbfs:.1} dBFS"),
        issues,
    })
}

/// Parse a MOV/MP4 container: track inventory and duration sanity. Essence
/// decode (ProRes etc.) is deliberately out of scope here. Tries the pure
/// Rust mp4 parser first; QuickTime files it cannot handle (ProRes MOVs
/// with tmcd tracks are the expected case) fall back to ffprobe when
/// installed.
pub fn probe_video(path: &Path) -> Result<MediaInfo> {
    match probe_video_mp4(path) {
        Ok(info) => Ok(info),
        Err(mp4_err) => match probe_video_ffprobe(path) {
            Ok(info) => Ok(info),
            Err(_) => Err(mp4_err),
        },
    }
}

fn build_video_info(
    path: &Path,
    duration: f64,
    video_tracks: usize,
    audio_tracks: usize,
    dims: Option<(u32, u32)>,
    via: &str,
) -> MediaInfo {
    let mut issues = Vec::new();
    if video_tracks == 0 {
        issues.push(Issue::NoVideoTrack);
    }
    if duration <= 0.0 {
        issues.push(Issue::ZeroDuration);
    }
    let dims_str = dims
        .map(|(w, h)| format!(", {w}x{h}"))
        .unwrap_or_default();
    MediaInfo {
        path: path.to_path_buf(),
        rel_path: path.file_name().unwrap_or_default().to_string_lossy().into_owned(),
        hash: None,
        kind: MediaKind::Video,
        duration_secs: Some(duration),
        start_timecode: None,
        detail: format!("{video_tracks} video / {audio_tracks} audio track(s){dims_str}{via}"),
        issues,
    }
}

fn probe_video_mp4(path: &Path) -> Result<MediaInfo> {
    let file = File::open(path).with_context(|| format!("cannot open {}", path.display()))?;
    let size = file.metadata()?.len();
    let mp4 = mp4::Mp4Reader::read_header(BufReader::new(file), size)
        .with_context(|| format!("cannot parse container {}", path.display()))?;

    let duration = mp4.duration().as_secs_f64();
    let mut video_tracks = 0usize;
    let mut audio_tracks = 0usize;
    let mut dims: Option<(u32, u32)> = None;
    for track in mp4.tracks().values() {
        match track.track_type() {
            Ok(mp4::TrackType::Video) => {
                video_tracks += 1;
                dims = Some((u32::from(track.width()), u32::from(track.height())));
            }
            Ok(mp4::TrackType::Audio) => audio_tracks += 1,
            _ => {}
        }
    }
    Ok(build_video_info(path, duration, video_tracks, audio_tracks, dims, ""))
}

fn ffprobe_json(path: &Path) -> Result<serde_json::Value> {
    let ffprobe =
        std::env::var("HARNESS_FFPROBE").unwrap_or_else(|_| "ffprobe".to_string());
    let out = std::process::Command::new(ffprobe)
        .args(["-v", "quiet", "-print_format", "json", "-show_streams", "-show_format"])
        .arg(path)
        .output()
        .context("ffprobe not runnable")?;
    if !out.status.success() {
        bail!("ffprobe failed on {}", path.display());
    }
    serde_json::from_slice(&out.stdout).context("unparseable ffprobe output")
}

fn probe_video_ffprobe(path: &Path) -> Result<MediaInfo> {
    let json = ffprobe_json(path)?;
    let empty = Vec::new();
    let streams = json["streams"].as_array().unwrap_or(&empty);
    let mut video_tracks = 0usize;
    let mut audio_tracks = 0usize;
    let mut dims: Option<(u32, u32)> = None;
    for s in streams {
        match s["codec_type"].as_str() {
            Some("video") => {
                video_tracks += 1;
                if let (Some(w), Some(h)) = (s["width"].as_u64(), s["height"].as_u64()) {
                    dims = Some((w as u32, h as u32));
                }
            }
            Some("audio") => audio_tracks += 1,
            _ => {}
        }
    }
    let duration: f64 = json["format"]["duration"]
        .as_str()
        .and_then(|d| d.parse().ok())
        .unwrap_or(0.0);
    Ok(build_video_info(path, duration, video_tracks, audio_tracks, dims, " (via ffprobe)"))
}

/// Start timecode from a `tmcd` track or container tag, via ffprobe.
/// Returns None when ffprobe is missing or the file carries no timecode —
/// informational either way until the rig's behavior is known.
pub fn probe_timecode(path: &Path) -> Option<String> {
    let json = ffprobe_json(path).ok()?;
    let stream_tc = json["streams"].as_array().and_then(|streams| {
        streams
            .iter()
            .find_map(|s| s["tags"]["timecode"].as_str())
    });
    stream_tc
        .or_else(|| json["format"]["tags"]["timecode"].as_str())
        .map(String::from)
}

#[derive(Debug)]
pub struct CheckReport {
    pub files: Vec<MediaInfo>,
    pub skipped: Vec<PathBuf>,
    /// Max minus min duration across all checked files (None if fewer than 2).
    pub sync_spread_secs: Option<f64>,
    pub sync_ok: bool,
}

impl CheckReport {
    pub fn is_clean(&self) -> bool {
        self.sync_ok && !self.files.is_empty() && self.files.iter().all(|f| f.issues.is_empty())
    }
}

impl fmt::Display for CheckReport {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        for info in &self.files {
            let duration = info
                .duration_secs
                .map(|d| format!("{d:.2}s"))
                .unwrap_or_else(|| "?".to_string());
            let status = if info.issues.is_empty() { "ok" } else { "FAIL" };
            writeln!(f, "{status}: {} ({duration}, {})", info.path.display(), info.detail)?;
            for issue in &info.issues {
                writeln!(f, "  - {issue}")?;
            }
        }
        for path in &self.skipped {
            writeln!(f, "skipped (not media): {}", path.display())?;
        }
        if let Some(spread) = self.sync_spread_secs {
            writeln!(
                f,
                "duration spread across files: {spread:.2}s ({})",
                if self.sync_ok { "within tolerance" } else { "EXCEEDS SYNC TOLERANCE" }
            )?;
        }
        if self.files.is_empty() {
            write!(f, "no media files found — capture check FAILED")
        } else if self.is_clean() {
            write!(f, "capture check passed")
        } else {
            write!(f, "capture check FAILED")
        }
    }
}

/// Check every media file in a capture directory and the duration agreement
/// across them. Nonzero exit at the CLI when this is not clean.
pub fn check_dir(dir: &Path, expect: &Expectations) -> Result<CheckReport> {
    if !dir.is_dir() {
        bail!("{} is not a directory", dir.display());
    }
    let mut files = Vec::new();
    let mut skipped = Vec::new();
    for dirent in WalkDir::new(dir).follow_links(false).sort_by_file_name() {
        let dirent = dirent.context("cannot walk capture directory")?;
        if !dirent.file_type().is_file() {
            continue;
        }
        let path = dirent.path();
        if dirent.file_name().to_string_lossy().starts_with('.') {
            continue;
        }
        let ext = path
            .extension()
            .map(|e| e.to_string_lossy().to_lowercase())
            .unwrap_or_default();
        if dirent.file_name().to_string_lossy() == CHECK_REPORT_FILE {
            continue;
        }
        let info = match ext.as_str() {
            "wav" | "flac" => probe_audio(path, expect),
            "mov" | "mp4" | "m4v" => probe_video(path),
            _ => {
                skipped.push(path.to_path_buf());
                continue;
            }
        };
        // A file that cannot be opened/parsed at all is a failed check on
        // that file, not an abort of the whole session check.
        let mut info = info.unwrap_or_else(|e| MediaInfo {
            path: path.to_path_buf(),
            rel_path: String::new(),
            hash: None,
            kind: if matches!(ext.as_str(), "wav" | "flac") { MediaKind::Audio } else { MediaKind::Video },
            duration_secs: None,
            start_timecode: None,
            detail: "unreadable".to_string(),
            issues: vec![Issue::Decode { message: format!("{e:#}") }],
        });
        info.rel_path = path
            .strip_prefix(dir)
            .unwrap_or(path)
            .to_string_lossy()
            .into_owned();
        info.hash = crate::store::hash_file(path).ok();
        if info.kind == MediaKind::Video {
            info.start_timecode = probe_timecode(path);
            if let Some(tc) = &info.start_timecode {
                info.detail.push_str(&format!(", tc {tc}"));
            }
        }
        files.push(info);
    }

    let durations: Vec<f64> = files.iter().filter_map(|f| f.duration_secs).collect();
    let (sync_spread_secs, sync_ok) = if durations.len() >= 2 {
        let max = durations.iter().cloned().fold(f64::MIN, f64::max);
        let min = durations.iter().cloned().fold(f64::MAX, f64::min);
        let spread = max - min;
        (Some(spread), spread <= expect.sync_tolerance_secs)
    } else {
        (None, true)
    };

    Ok(CheckReport { files, skipped, sync_spread_secs, sync_ok })
}

// --- machine-readable report -------------------------------------------------
//
// Written by `harness check --json-report`, read back by `harness ingest`,
// which turns each file entry into a provenance sidecar next to the stored
// original (ADC-007's sidecar rule applied to capture-quality provenance).

/// Conventional report filename inside a capture directory. `check` never
/// probes it as media and `ingest` never stores it as an original.
pub const CHECK_REPORT_FILE: &str = "capture-check.json";
pub const REPORT_VERSION: &str = "0.1.0";

#[derive(Debug, Serialize, Deserialize)]
pub struct JsonReport {
    pub report_version: String,
    pub checked_at: String,
    pub tool: crate::manifest::Tool,
    pub expectations: Expectations,
    pub clean: bool,
    pub sync_ok: bool,
    pub sync_spread_secs: Option<f64>,
    pub files: Vec<JsonFileEntry>,
    pub skipped: Vec<String>,
}

/// Issues are carried as raw JSON so the reader does not need to keep pace
/// with the `Issue` enum; sidecars preserve them verbatim.
#[derive(Debug, Serialize, Deserialize)]
pub struct JsonFileEntry {
    pub rel_path: String,
    pub hash: Option<String>,
    pub kind: String,
    pub duration_secs: Option<f64>,
    #[serde(default)]
    pub start_timecode: Option<String>,
    pub detail: String,
    pub issues: Vec<serde_json::Value>,
}

pub fn build_json_report(report: &CheckReport, expect: &Expectations) -> Result<JsonReport> {
    let files = report
        .files
        .iter()
        .map(|f| {
            let issues = f
                .issues
                .iter()
                .map(serde_json::to_value)
                .collect::<Result<Vec<_>, _>>()
                .context("cannot serialize check issues")?;
            Ok(JsonFileEntry {
                rel_path: f.rel_path.clone(),
                hash: f.hash.clone(),
                kind: match f.kind {
                    MediaKind::Audio => "audio".to_string(),
                    MediaKind::Video => "video".to_string(),
                },
                duration_secs: f.duration_secs,
                start_timecode: f.start_timecode.clone(),
                detail: f.detail.clone(),
                issues,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    Ok(JsonReport {
        report_version: REPORT_VERSION.to_string(),
        checked_at: humantime::format_rfc3339_seconds(std::time::SystemTime::now()).to_string(),
        tool: crate::manifest::Tool::this(),
        expectations: expect.clone(),
        clean: report.is_clean(),
        sync_ok: report.sync_ok,
        sync_spread_secs: report.sync_spread_secs,
        files,
        skipped: report
            .skipped
            .iter()
            .map(|p| p.to_string_lossy().into_owned())
            .collect(),
    })
}

pub fn write_json_report(report: &JsonReport, path: &Path) -> Result<()> {
    let json = serde_json::to_string_pretty(report).context("cannot serialize check report")?;
    std::fs::write(path, json)
        .with_context(|| format!("cannot write check report to {}", path.display()))?;
    Ok(())
}

pub fn load_json_report(path: &Path) -> Result<JsonReport> {
    let bytes = std::fs::read(path)
        .with_context(|| format!("cannot read check report {}", path.display()))?;
    serde_json::from_slice(&bytes)
        .with_context(|| format!("malformed check report {}", path.display()))
}
