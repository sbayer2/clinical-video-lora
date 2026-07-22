//! Content-addressed immutable ingest (ADC-004, ADC-007).
//!
//! Invariants this module must never break:
//! - a stored original is never overwritten or modified;
//! - a manifest entry is written only after the stored copy's bytes have
//!   been independently re-hashed and matched against the source;
//! - a failed or interrupted copy leaves at most a `.partial` file, which
//!   `validate` reports as an orphan.

use std::collections::HashSet;
use std::fmt;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

use anyhow::{Context, Result, bail};
use walkdir::WalkDir;

use crate::manifest::{self, Entry, MANIFEST_VERSION, Tool};

pub const ORIGINALS_DIR: &str = "originals";

#[derive(Debug, Default)]
pub struct IngestReport {
    pub ingested: usize,
    pub skipped_duplicate: usize,
    pub ingested_bytes: u64,
}

impl fmt::Display for IngestReport {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "ingested {} file(s) ({} bytes), skipped {} duplicate(s)",
            self.ingested, self.ingested_bytes, self.skipped_duplicate
        )
    }
}

pub fn hash_file(path: &Path) -> Result<String> {
    let file = fs::File::open(path).with_context(|| format!("cannot open {}", path.display()))?;
    let mut hasher = blake3::Hasher::new();
    hasher
        .update_reader(file)
        .with_context(|| format!("cannot hash {}", path.display()))?;
    Ok(hasher.finalize().to_hex().to_string())
}

/// Walk `src` and ingest every regular non-hidden file into `store`.
/// Duplicate content (same hash, anywhere in the manifest) is skipped.
pub fn ingest_dir(src: &Path, store: &Path) -> Result<IngestReport> {
    if !src.is_dir() {
        bail!("source {} is not a directory", src.display());
    }
    fs::create_dir_all(store.join(ORIGINALS_DIR))
        .with_context(|| format!("cannot create store at {}", store.display()))?;

    let mut known: HashSet<String> = manifest::load(store)?
        .into_iter()
        .map(|e| e.hash)
        .collect();

    let mut report = IngestReport::default();
    for dirent in WalkDir::new(src).follow_links(false).sort_by_file_name() {
        let dirent = dirent.context("cannot walk source directory")?;
        if !dirent.file_type().is_file() {
            continue;
        }
        if dirent.file_name().to_string_lossy().starts_with('.') {
            continue;
        }
        match ingest_file(dirent.path(), store, &known)? {
            Some(entry) => {
                report.ingested += 1;
                report.ingested_bytes += entry.size_bytes;
                known.insert(entry.hash);
            }
            None => report.skipped_duplicate += 1,
        }
    }
    Ok(report)
}

/// Ingest one file. Returns `None` if its content is already in the store.
fn ingest_file(path: &Path, store: &Path, known: &HashSet<String>) -> Result<Option<Entry>> {
    let hash = hash_file(path)?;
    if known.contains(&hash) {
        return Ok(None);
    }

    let ext = path
        .extension()
        .map(|e| e.to_string_lossy().to_lowercase());
    let file_name = match &ext {
        Some(e) => format!("{hash}.{e}"),
        None => hash.clone(),
    };
    let rel = PathBuf::from(ORIGINALS_DIR).join(&hash[..2]).join(file_name);
    let dest = store.join(&rel);
    fs::create_dir_all(dest.parent().expect("dest has a parent"))
        .with_context(|| format!("cannot create shard dir for {}", dest.display()))?;
    if dest.exists() {
        // Same content already on disk but absent from the manifest: never
        // touch the existing file; surface the inconsistency instead.
        bail!(
            "{} exists in the store but not in the manifest; run `harness validate` \
             and reconcile before ingesting",
            dest.display()
        );
    }

    // Stage, verify the stored bytes independently, then rename into place.
    let partial = dest.with_file_name(format!(
        "{}.partial",
        dest.file_name().expect("dest has a file name").to_string_lossy()
    ));
    fs::copy(path, &partial)
        .with_context(|| format!("cannot copy {} into store", path.display()))?;
    let stored_hash = hash_file(&partial)?;
    if stored_hash != hash {
        let _ = fs::remove_file(&partial);
        bail!(
            "copy verification failed for {}: source changed during ingest or copy corrupted",
            path.display()
        );
    }
    let size_bytes = fs::metadata(&partial)?.len();
    let mut perms = fs::metadata(&partial)?.permissions();
    perms.set_readonly(true);
    fs::set_permissions(&partial, perms)
        .with_context(|| format!("cannot mark {} read-only", partial.display()))?;
    fs::rename(&partial, &dest)
        .with_context(|| format!("cannot move {} into place", partial.display()))?;

    let entry = Entry {
        manifest_version: MANIFEST_VERSION.to_string(),
        hash,
        store_path: rel.to_string_lossy().into_owned(),
        original_name: path
            .file_name()
            .expect("source has a file name")
            .to_string_lossy()
            .into_owned(),
        size_bytes,
        ingested_at: humantime::format_rfc3339_seconds(SystemTime::now()).to_string(),
        tool: Tool::this(),
    };
    manifest::append(store, &entry)?;
    Ok(Some(entry))
}
