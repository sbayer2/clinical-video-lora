use std::fs;
use std::path::PathBuf;

use harness::{store, validate};

fn fresh_dir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("harness-test-{name}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

fn make_writable(path: &std::path::Path) {
    let mut perms = fs::metadata(path).unwrap().permissions();
    #[allow(clippy::permissions_set_readonly_false)]
    perms.set_readonly(false);
    fs::set_permissions(path, perms).unwrap();
}

#[test]
fn ingest_then_validate_clean_and_dedup() {
    let root = fresh_dir("clean");
    let src = root.join("src");
    fs::create_dir_all(&src).unwrap();
    fs::write(src.join("a.wav"), b"RIFF fake audio payload").unwrap();
    fs::write(src.join("b.mov"), b"moov fake video payload").unwrap();
    fs::write(src.join(".DS_Store"), b"junk").unwrap();
    let store_dir = root.join("store");

    let report = store::ingest_dir(&src, &store_dir).unwrap();
    assert_eq!(report.ingested, 2, "hidden files must be skipped");

    let again = store::ingest_dir(&src, &store_dir).unwrap();
    assert_eq!(again.ingested, 0);
    assert_eq!(again.skipped_duplicate, 2);

    let v = validate::validate_store(&store_dir).unwrap();
    assert!(v.is_clean(), "expected clean store, got: {v}");
    assert_eq!(v.ok, 2);
}

#[test]
fn stored_originals_are_readonly() {
    let root = fresh_dir("readonly");
    let src = root.join("src");
    fs::create_dir_all(&src).unwrap();
    fs::write(src.join("a.wav"), b"payload").unwrap();
    let store_dir = root.join("store");
    store::ingest_dir(&src, &store_dir).unwrap();

    let entry = &harness::manifest::load(&store_dir).unwrap()[0];
    let stored = store_dir.join(&entry.store_path);
    assert!(fs::metadata(&stored).unwrap().permissions().readonly());
    assert!(fs::write(&stored, b"overwrite attempt").is_err());
}

#[test]
fn validate_detects_corruption_and_orphans() {
    let root = fresh_dir("corrupt");
    let src = root.join("src");
    fs::create_dir_all(&src).unwrap();
    fs::write(src.join("a.wav"), b"original payload!").unwrap();
    let store_dir = root.join("store");
    store::ingest_dir(&src, &store_dir).unwrap();

    // Same-length bit flip: only the hash check can catch this.
    let entry = &harness::manifest::load(&store_dir).unwrap()[0];
    let stored = store_dir.join(&entry.store_path);
    make_writable(&stored);
    fs::write(&stored, b"tampered payload!").unwrap();

    // A file the manifest knows nothing about.
    let orphan = store_dir.join("originals").join("zz").join("stray.partial");
    fs::create_dir_all(orphan.parent().unwrap()).unwrap();
    fs::write(&orphan, b"leftover").unwrap();

    let v = validate::validate_store(&store_dir).unwrap();
    assert!(!v.is_clean());
    assert_eq!(v.corrupted.len(), 1);
    assert_eq!(v.orphans.len(), 1);
    assert_eq!(v.ok, 0);
}

#[test]
fn validate_detects_missing_and_size_mismatch() {
    let root = fresh_dir("missing");
    let src = root.join("src");
    fs::create_dir_all(&src).unwrap();
    fs::write(src.join("a.wav"), b"first payload").unwrap();
    fs::write(src.join("b.wav"), b"second payload").unwrap();
    let store_dir = root.join("store");
    store::ingest_dir(&src, &store_dir).unwrap();

    let entries = harness::manifest::load(&store_dir).unwrap();
    let first = store_dir.join(&entries[0].store_path);
    fs::remove_file(&first).unwrap();
    let second = store_dir.join(&entries[1].store_path);
    make_writable(&second);
    fs::write(&second, b"now a different, longer payload").unwrap();

    let v = validate::validate_store(&store_dir).unwrap();
    assert_eq!(v.missing.len(), 1);
    assert_eq!(v.size_mismatch.len(), 1);
    assert!(!v.is_clean());
}
