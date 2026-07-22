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
use symphonia::core::audio::SampleBuffer;
use symphonia::core::codecs::DecoderOptions;
use symphonia::core::errors::Error as SymError;
use symphonia::core::formats::FormatOptions;
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::MetadataOptions;
use symphonia::core::probe::Hint;
use walkdir::WalkDir;

/// Capture-rig expectations from plan section 3, overridable on the CLI.
#[derive(Debug, Clone)]
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

#[derive(Debug)]
pub enum Issue {
    Decode(String),
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
            Issue::Decode(msg) => write!(f, "decode failure: {msg}"),
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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MediaKind {
    Audio,
    Video,
}

#[derive(Debug)]
pub struct MediaInfo {
    pub path: PathBuf,
    pub kind: MediaKind,
    pub duration_secs: Option<f64>,
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
                issues.push(Issue::Decode("unexpected mid-stream reset".to_string()));
                break;
            }
            Err(e) => {
                issues.push(Issue::Decode(e.to_string()));
                break;
            }
        };
        if packet.track_id() != track_id {
            continue;
        }
        let decoded = match decoder.decode(&packet) {
            Ok(decoded) => decoded,
            Err(e) => {
                issues.push(Issue::Decode(e.to_string()));
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
        kind: MediaKind::Audio,
        duration_secs: Some(frames as f64 / f64::from(sample_rate)),
        detail: format!("{sample_rate} Hz, {channels} ch, {bits}, peak {peak_dbfs:.1} dBFS"),
        issues,
    })
}

/// Parse a MOV/MP4 container: track inventory and duration sanity. Essence
/// decode (ProRes etc.) is deliberately out of scope here.
pub fn probe_video(path: &Path) -> Result<MediaInfo> {
    let file = File::open(path).with_context(|| format!("cannot open {}", path.display()))?;
    let size = file.metadata()?.len();
    let mp4 = mp4::Mp4Reader::read_header(BufReader::new(file), size)
        .with_context(|| format!("cannot parse container {}", path.display()))?;

    let duration = mp4.duration().as_secs_f64();
    let mut video_tracks = 0usize;
    let mut audio_tracks = 0usize;
    let mut dims: Option<(u16, u16)> = None;
    for track in mp4.tracks().values() {
        match track.track_type() {
            Ok(mp4::TrackType::Video) => {
                video_tracks += 1;
                dims = Some((track.width(), track.height()));
            }
            Ok(mp4::TrackType::Audio) => audio_tracks += 1,
            _ => {}
        }
    }

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
    Ok(MediaInfo {
        path: path.to_path_buf(),
        kind: MediaKind::Video,
        duration_secs: Some(duration),
        detail: format!("{video_tracks} video / {audio_tracks} audio track(s){dims_str}"),
        issues,
    })
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
        files.push(info.unwrap_or_else(|e| MediaInfo {
            path: path.to_path_buf(),
            kind: if matches!(ext.as_str(), "wav" | "flac") { MediaKind::Audio } else { MediaKind::Video },
            duration_secs: None,
            detail: "unreadable".to_string(),
            issues: vec![Issue::Decode(format!("{e:#}"))],
        }));
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
