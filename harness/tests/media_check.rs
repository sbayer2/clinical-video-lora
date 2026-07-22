use std::fs;
use std::path::{Path, PathBuf};

use harness::media::{self, Expectations, Issue, check_dir, probe_audio, probe_video};

fn fresh_dir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("harness-media-{name}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

/// Minimal 24-bit PCM WAV writer; `samples` is interleaved in [-1, 1].
fn write_wav_24(path: &Path, sample_rate: u32, channels: u16, samples: &[f32]) {
    let data_len = (samples.len() * 3) as u32;
    let mut buf: Vec<u8> = Vec::with_capacity(44 + samples.len() * 3);
    buf.extend(b"RIFF");
    buf.extend(&(36 + data_len).to_le_bytes());
    buf.extend(b"WAVE");
    buf.extend(b"fmt ");
    buf.extend(&16u32.to_le_bytes());
    buf.extend(&1u16.to_le_bytes()); // PCM
    buf.extend(&channels.to_le_bytes());
    buf.extend(&sample_rate.to_le_bytes());
    buf.extend(&(sample_rate * u32::from(channels) * 3).to_le_bytes());
    buf.extend(&(channels * 3).to_le_bytes());
    buf.extend(&24u16.to_le_bytes());
    buf.extend(b"data");
    buf.extend(&data_len.to_le_bytes());
    for &s in samples {
        let v = (s.clamp(-1.0, 1.0) * 8_388_607.0) as i32;
        buf.extend(&v.to_le_bytes()[..3]);
    }
    fs::write(path, buf).unwrap();
}

/// Interleaved stereo sine at `amp`, `secs` long.
fn stereo_sine(sample_rate: u32, secs: f64, amp: f32) -> Vec<f32> {
    let frames = (f64::from(sample_rate) * secs) as usize;
    let mut out = Vec::with_capacity(frames * 2);
    for i in 0..frames {
        let t = i as f32 / sample_rate as f32;
        let s = amp * (2.0 * std::f32::consts::PI * 220.0 * t).sin();
        out.push(s);
        out.push(s);
    }
    out
}

fn expectations() -> Expectations {
    Expectations::default()
}

#[test]
fn clean_track_passes() {
    let dir = fresh_dir("clean");
    let path = dir.join("track_a.wav");
    write_wav_24(&path, 48_000, 2, &stereo_sine(48_000, 0.5, 0.5));
    let info = probe_audio(&path, &expectations()).unwrap();
    assert!(info.issues.is_empty(), "unexpected issues: {:?}", info.issues);
    let d = info.duration_secs.unwrap();
    assert!((d - 0.5).abs() < 0.01, "duration {d} not ~0.5s");
}

#[test]
fn dead_channel_is_flagged() {
    let dir = fresh_dir("dead");
    let path = dir.join("track.wav");
    let mut samples = stereo_sine(48_000, 0.25, 0.5);
    for frame in samples.chunks_mut(2) {
        frame[1] = 0.0;
    }
    write_wav_24(&path, 48_000, 2, &samples);
    let info = probe_audio(&path, &expectations()).unwrap();
    assert!(
        info.issues
            .iter()
            .any(|i| matches!(i, Issue::SilentChannel { channel: 1, .. })),
        "expected SilentChannel(1), got: {:?}",
        info.issues
    );
}

#[test]
fn clipping_is_flagged() {
    let dir = fresh_dir("clip");
    let path = dir.join("track.wav");
    let samples: Vec<f32> = (0..48_000)
        .flat_map(|i| {
            let s = if (i / 100) % 2 == 0 { 1.0 } else { -1.0 };
            [s, s * 0.5]
        })
        .collect();
    write_wav_24(&path, 48_000, 2, &samples);
    let info = probe_audio(&path, &expectations()).unwrap();
    assert!(
        info.issues
            .iter()
            .any(|i| matches!(i, Issue::Clipping { channel: 0, .. })),
        "expected Clipping(0), got: {:?}",
        info.issues
    );
    assert!(
        !info
            .issues
            .iter()
            .any(|i| matches!(i, Issue::Clipping { channel: 1, .. })),
        "channel 1 must not clip"
    );
}

#[test]
fn wrong_sample_rate_is_flagged() {
    let dir = fresh_dir("rate");
    let path = dir.join("track.wav");
    write_wav_24(&path, 44_100, 2, &stereo_sine(44_100, 0.25, 0.5));
    let info = probe_audio(&path, &expectations()).unwrap();
    assert!(
        info.issues
            .iter()
            .any(|i| matches!(i, Issue::UnexpectedSampleRate { found: 44_100, .. })),
        "expected UnexpectedSampleRate, got: {:?}",
        info.issues
    );
}

#[test]
fn truncated_file_is_flagged() {
    let dir = fresh_dir("trunc");
    let path = dir.join("track.wav");
    write_wav_24(&path, 48_000, 2, &stereo_sine(48_000, 0.5, 0.5));
    // Chop off the last quarter of the payload without fixing the header.
    let bytes = fs::read(&path).unwrap();
    fs::write(&path, &bytes[..bytes.len() - bytes.len() / 4]).unwrap();
    let info = probe_audio(&path, &expectations()).unwrap();
    assert!(
        info.issues.iter().any(|i| matches!(i, Issue::Truncated { .. })),
        "expected Truncated, got: {:?}",
        info.issues
    );
}

#[test]
fn duration_disagreement_fails_sync_check() {
    let dir = fresh_dir("sync");
    write_wav_24(&dir.join("a.wav"), 48_000, 2, &stereo_sine(48_000, 0.5, 0.5));
    write_wav_24(&dir.join("b.wav"), 48_000, 2, &stereo_sine(48_000, 3.0, 0.5));
    let report = check_dir(&dir, &expectations()).unwrap();
    assert!(!report.sync_ok);
    assert!(!report.is_clean());
    let spread = report.sync_spread_secs.unwrap();
    assert!((spread - 2.5).abs() < 0.05, "spread {spread} not ~2.5s");
}

#[test]
fn matching_durations_pass_sync_check() {
    let dir = fresh_dir("sync-ok");
    write_wav_24(&dir.join("a.wav"), 48_000, 2, &stereo_sine(48_000, 0.5, 0.5));
    write_wav_24(&dir.join("b.wav"), 48_000, 2, &stereo_sine(48_000, 0.5, 0.4));
    fs::write(dir.join("notes.txt"), "not media").unwrap();
    let report = check_dir(&dir, &expectations()).unwrap();
    assert!(report.sync_ok);
    assert!(report.is_clean(), "expected clean report, got: {report}");
    assert_eq!(report.skipped.len(), 1);
}

#[test]
fn unreadable_media_is_a_failed_check_not_an_abort() {
    let dir = fresh_dir("garbage");
    fs::write(dir.join("broken.wav"), b"not a wav at all").unwrap();
    let report = check_dir(&dir, &expectations()).unwrap();
    assert_eq!(report.files.len(), 1);
    assert!(!report.is_clean());
    assert!(
        report.files[0].issues.iter().any(|i| matches!(i, Issue::Decode { .. })),
        "expected Decode issue, got: {:?}",
        report.files[0].issues
    );
}

#[test]
fn empty_capture_dir_fails() {
    let dir = fresh_dir("empty");
    let report = check_dir(&dir, &expectations()).unwrap();
    assert!(!report.is_clean(), "an empty capture dir must never pass");
}

/// Gated on ffmpeg/ffprobe being installed: generates a real ProRes MOV
/// with a tmcd timecode track — the format the capture rig will write —
/// and verifies check_dir reads the start timecode and accepts the file.
#[test]
fn prores_mov_timecode_is_read_when_ffmpeg_available() {
    use std::process::Command;
    let have = |bin: &str| Command::new(bin).arg("-version").output().is_ok();
    if !have("ffmpeg") || !have("ffprobe") {
        eprintln!("skipping: ffmpeg/ffprobe not installed");
        return;
    }

    let dir = fresh_dir("tmcd");
    let path = dir.join("cam1.mov");
    let status = Command::new("ffmpeg")
        .args([
            "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=128x72:rate=30",
            "-t", "1", "-timecode", "01:02:03:04",
            "-c:v", "prores",
        ])
        .arg(&path)
        .status()
        .unwrap();
    assert!(status.success(), "ffmpeg failed to generate ProRes test file");

    assert_eq!(media::probe_timecode(&path).as_deref(), Some("01:02:03:04"));

    let report = check_dir(&dir, &expectations()).unwrap();
    assert_eq!(report.files.len(), 1);
    let info = &report.files[0];
    assert_eq!(info.kind, media::MediaKind::Video);
    assert_eq!(info.start_timecode.as_deref(), Some("01:02:03:04"));
    assert!(
        info.issues.is_empty(),
        "ProRes MOV with tmcd track must pass the container check, got: {:?}",
        info.issues
    );
    let d = info.duration_secs.unwrap();
    assert!((d - 1.0).abs() < 0.1, "duration {d} not ~1s");
}

#[test]
fn mp4_container_parses_and_missing_video_track_is_flagged() {
    use mp4::{
        AacConfig, AudioObjectType, ChannelConfig, MediaConfig, Mp4Config, Mp4Sample, Mp4Writer,
        SampleFreqIndex, TrackConfig, TrackType,
    };

    let dir = fresh_dir("mp4");
    let path = dir.join("audio_only.mp4");
    let file = fs::File::create(&path).unwrap();
    let config = Mp4Config {
        major_brand: str::parse("isom").unwrap(),
        minor_version: 512,
        compatible_brands: vec![str::parse("isom").unwrap(), str::parse("iso2").unwrap()],
        timescale: 1000,
    };
    let mut writer = Mp4Writer::write_start(file, &config).unwrap();
    writer
        .add_track(&TrackConfig {
            track_type: TrackType::Audio,
            timescale: 48_000,
            language: "und".to_string(),
            media_conf: MediaConfig::AacConfig(AacConfig {
                bitrate: 64_000,
                profile: AudioObjectType::AacLowComplexity,
                freq_index: SampleFreqIndex::Freq48000,
                chan_conf: ChannelConfig::Stereo,
            }),
        })
        .unwrap();
    writer
        .write_sample(
            1,
            &Mp4Sample {
                start_time: 0,
                duration: 48_000, // one second at the track timescale
                rendering_offset: 0,
                is_sync: true,
                bytes: bytes::Bytes::from_static(&[0u8; 16]),
            },
        )
        .unwrap();
    writer.write_end().unwrap();

    let info = probe_video(&path).unwrap();
    assert_eq!(info.kind, media::MediaKind::Video);
    assert!(info.duration_secs.unwrap() > 0.5);
    assert!(
        info.issues.iter().any(|i| matches!(i, Issue::NoVideoTrack)),
        "expected NoVideoTrack for an audio-only container, got: {:?}",
        info.issues
    );
}
