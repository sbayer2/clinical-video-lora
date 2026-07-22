//! The check → ingest provenance flow: `harness check --json-report` output
//! becomes per-file sidecars under `provenance/` at ingest time.

use std::fs;
use std::path::{Path, PathBuf};

use harness::media::{self, Expectations};
use harness::store::{self, IngestOptions};

fn fresh_dir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("harness-prov-{name}-{}", std::process::id()));
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
    buf.extend(&1u16.to_le_bytes());
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

fn stereo_sine(sample_rate: u32, secs: f64, amp_right: f32) -> Vec<f32> {
    let frames = (f64::from(sample_rate) * secs) as usize;
    let mut out = Vec::with_capacity(frames * 2);
    for i in 0..frames {
        let t = i as f32 / sample_rate as f32;
        let s = (2.0 * std::f32::consts::PI * 220.0 * t).sin();
        out.push(0.5 * s);
        out.push(amp_right * s);
    }
    out
}

/// Run check, write the conventional report into the capture dir, return clean flag.
fn check_and_write_report(src: &Path) -> bool {
    let expect = Expectations::default();
    let report = media::check_dir(src, &expect).unwrap();
    let json = media::build_json_report(&report, &expect).unwrap();
    media::write_json_report(&json, &src.join(media::CHECK_REPORT_FILE)).unwrap();
    report.is_clean()
}

#[test]
fn clean_check_becomes_sidecars_at_ingest() {
    let root = fresh_dir("clean-flow");
    let src = root.join("capture");
    fs::create_dir_all(&src).unwrap();
    write_wav_24(&src.join("a.wav"), 48_000, 2, &stereo_sine(48_000, 0.5, 0.5));
    write_wav_24(&src.join("b.wav"), 48_000, 2, &stereo_sine(48_000, 0.5, 0.4));
    assert!(check_and_write_report(&src));

    let store_dir = root.join("store");
    let report = store::ingest_dir(&src, &store_dir, &IngestOptions::default()).unwrap();
    assert_eq!(report.ingested, 2, "the report file must not be ingested as an original");
    assert_eq!(report.check_clean, Some(true));
    assert_eq!(report.sidecars_written, 2);
    assert_eq!(report.unmatched_by_check, 0);

    // Each manifest entry has a sidecar keyed by its hash, and the sidecar's
    // recorded hash matches.
    let entries = harness::manifest::load(&store_dir).unwrap();
    assert_eq!(entries.len(), 2);
    for entry in &entries {
        let sidecar_path = store_dir
            .join(store::PROVENANCE_DIR)
            .join(format!("{}.capture-check.json", entry.hash));
        let sidecar: serde_json::Value =
            serde_json::from_slice(&fs::read(&sidecar_path).unwrap()).unwrap();
        assert_eq!(sidecar["file"]["hash"], serde_json::json!(entry.hash));
        assert_eq!(sidecar["check"]["clean"], serde_json::json!(true));
        assert!(sidecar["file"]["issues"].as_array().unwrap().is_empty());
    }

    // Provenance files must not trip the fixity orphan scan.
    let v = harness::validate::validate_store(&store_dir).unwrap();
    assert!(v.is_clean(), "provenance sidecars flagged by validate: {v}");
}

#[test]
fn failed_check_gates_ingest_unless_overridden() {
    let root = fresh_dir("gate");
    let src = root.join("capture");
    fs::create_dir_all(&src).unwrap();
    // Dead right channel -> check is not clean.
    write_wav_24(&src.join("bad.wav"), 48_000, 2, &stereo_sine(48_000, 0.5, 0.0));
    assert!(!check_and_write_report(&src));

    let store_dir = root.join("store");
    let err = store::ingest_dir(&src, &store_dir, &IngestOptions::default()).unwrap_err();
    assert!(
        err.to_string().contains("not clean"),
        "expected failed-check gate, got: {err:#}"
    );
    assert!(harness::manifest::load(&store_dir).unwrap().is_empty());

    let report = store::ingest_dir(
        &src,
        &store_dir,
        &IngestOptions { allow_failed_check: true },
    )
    .unwrap();
    assert_eq!(report.ingested, 1);
    assert_eq!(report.check_clean, Some(false));
    assert_eq!(report.sidecars_written, 1);

    // The sidecar preserves the failure evidence.
    let entry = &harness::manifest::load(&store_dir).unwrap()[0];
    let sidecar_path = store_dir
        .join(store::PROVENANCE_DIR)
        .join(format!("{}.capture-check.json", entry.hash));
    let sidecar: serde_json::Value =
        serde_json::from_slice(&fs::read(&sidecar_path).unwrap()).unwrap();
    assert_eq!(sidecar["check"]["clean"], serde_json::json!(false));
    let issues = sidecar["file"]["issues"].as_array().unwrap();
    assert!(
        issues.iter().any(|i| i["type"] == "silent_channel"),
        "expected silent_channel issue in sidecar, got: {issues:?}"
    );
}

#[test]
fn reingest_writes_no_duplicate_sidecars_and_stale_files_are_counted() {
    let root = fresh_dir("reingest");
    let src = root.join("capture");
    fs::create_dir_all(&src).unwrap();
    write_wav_24(&src.join("a.wav"), 48_000, 2, &stereo_sine(48_000, 0.5, 0.5));
    assert!(check_and_write_report(&src));

    let store_dir = root.join("store");
    let first = store::ingest_dir(&src, &store_dir, &IngestOptions::default()).unwrap();
    assert_eq!(first.sidecars_written, 1);

    // A file added after the check ran: ingested, but flagged as unchecked.
    write_wav_24(&src.join("late.wav"), 48_000, 2, &stereo_sine(48_000, 0.5, 0.3));
    let second = store::ingest_dir(&src, &store_dir, &IngestOptions::default()).unwrap();
    assert_eq!(second.skipped_duplicate, 1);
    assert_eq!(second.ingested, 1);
    assert_eq!(second.sidecars_written, 0, "existing sidecar must not be rewritten");
    assert_eq!(second.unmatched_by_check, 1);
}
