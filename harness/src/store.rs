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
use serde::Serialize;
use walkdir::WalkDir;

use crate::manifest::{self, Entry, MANIFEST_VERSION, Tool};
use crate::media::{self, JsonFileEntry, JsonReport};

pub const ORIGINALS_DIR: &str = "originals";
pub const PROVENANCE_DIR: &str = "provenance";
pub const SIDECAR_VERSION: &str = "0.1.0";

#[derive(Debug, Default, Clone)]
pub struct IngestOptions {
    /// Ingest even when the capture-check report says the session failed.
    pub allow_failed_check: bool,
}

#[derive(Debug, Default)]
pub struct IngestReport {
    pub ingested: usize,
    pub skipped_duplicate: usize,
    pub ingested_bytes: u64,
    /// `clean` flag of the capture-check report, if one was found.
    pub check_clean: Option<bool>,
    pub sidecars_written: usize,
    /// Files ingested that the capture-check report does not mention —
    /// they appeared (or changed) after the check ran.
    pub unmatched_by_check: usize,
}

impl fmt::Display for IngestReport {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "ingested {} file(s) ({} bytes), skipped {} duplicate(s)",
            self.ingested, self.ingested_bytes, self.skipped_duplicate
        )?;
        match self.check_clean {
            Some(clean) => {
                write!(
                    f,
                    "; capture check {}, {} provenance sidecar(s) written",
                    if clean { "clean" } else { "FAILED (ingested anyway)" },
                    self.sidecars_written
                )?;
                if self.unmatched_by_check > 0 {
                    write!(
                        f,
                        "; WARNING: {} file(s) not covered by the capture check",
                        self.unmatched_by_check
                    )?;
                }
            }
            None => write!(f, "; no capture-check report found — run `harness check` first")?,
        }
        Ok(())
    }
}

/// Provenance sidecar written next to (not inside) `originals/`: the
/// capture-check result for one stored file, keyed by content hash.
#[derive(Debug, Serialize)]
struct CheckSidecar<'a> {
    sidecar_version: &'a str,
    written_at: String,
    ingest_tool: Tool,
    /// Report-level context the per-file entry was produced under.
    check: SidecarReportMeta<'a>,
    file: &'a JsonFileEntry,
}

#[derive(Debug, Serialize)]
struct SidecarReportMeta<'a> {
    report_version: &'a str,
    checked_at: &'a str,
    tool: &'a Tool,
    expectations: &'a media::Expectations,
    clean: bool,
    sync_ok: bool,
    sync_spread_secs: Option<f64>,
}

fn write_sidecar(store: &Path, hash: &str, report: &JsonReport, entry: &JsonFileEntry) -> Result<bool> {
    let dir = store.join(PROVENANCE_DIR);
    fs::create_dir_all(&dir).context("cannot create provenance directory")?;
    let path = dir.join(format!("{hash}.capture-check.json"));
    if path.exists() {
        return Ok(false);
    }
    let sidecar = CheckSidecar {
        sidecar_version: SIDECAR_VERSION,
        written_at: humantime::format_rfc3339_seconds(SystemTime::now()).to_string(),
        ingest_tool: Tool::this(),
        check: SidecarReportMeta {
            report_version: &report.report_version,
            checked_at: &report.checked_at,
            tool: &report.tool,
            expectations: &report.expectations,
            clean: report.clean,
            sync_ok: report.sync_ok,
            sync_spread_secs: report.sync_spread_secs,
        },
        file: entry,
    };
    let json = serde_json::to_string_pretty(&sidecar).context("cannot serialize sidecar")?;
    fs::write(&path, json).with_context(|| format!("cannot write sidecar {}", path.display()))?;
    Ok(true)
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
///
/// If `src` contains a `capture-check.json` (written by `harness check
/// --json-report`), ingest is gated on it being clean (unless
/// `allow_failed_check`), the report file itself is not stored as an
/// original, and each stored file matched by hash gets a provenance
/// sidecar under `provenance/`.
pub fn ingest_dir(src: &Path, store: &Path, opts: &IngestOptions) -> Result<IngestReport> {
    if !src.is_dir() {
        bail!("source {} is not a directory", src.display());
    }

    let check_report_path = src.join(media::CHECK_REPORT_FILE);
    let check_report = if check_report_path.is_file() {
        Some(media::load_json_report(&check_report_path)?)
    } else {
        None
    };
    if let Some(report) = &check_report {
        if !report.clean && !opts.allow_failed_check {
            bail!(
                "capture check in {} is not clean; fix the capture or pass --allow-failed-check",
                check_report_path.display()
            );
        }
    }
    let check_by_hash: std::collections::HashMap<&str, &JsonFileEntry> = check_report
        .iter()
        .flat_map(|r| r.files.iter())
        .filter_map(|f| f.hash.as_deref().map(|h| (h, f)))
        .collect();

    fs::create_dir_all(store.join(ORIGINALS_DIR))
        .with_context(|| format!("cannot create store at {}", store.display()))?;

    let mut known: HashSet<String> = manifest::load(store)?
        .into_iter()
        .map(|e| e.hash)
        .collect();

    let mut report = IngestReport {
        check_clean: check_report.as_ref().map(|r| r.clean),
        ..IngestReport::default()
    };
    for dirent in WalkDir::new(src).follow_links(false).sort_by_file_name() {
        let dirent = dirent.context("cannot walk source directory")?;
        if !dirent.file_type().is_file() {
            continue;
        }
        let name = dirent.file_name().to_string_lossy();
        if name.starts_with('.') || name == media::CHECK_REPORT_FILE {
            continue;
        }
        let path = dirent.path();
        let hash = hash_file(path)?;

        // Provenance applies to duplicates too: the content is in the store
        // either way, and its capture-check result should be recorded once.
        if let Some(check_report) = &check_report {
            match check_by_hash.get(hash.as_str()) {
                Some(entry) => {
                    if write_sidecar(store, &hash, check_report, entry)? {
                        report.sidecars_written += 1;
                    }
                }
                None => report.unmatched_by_check += 1,
            }
        }

        if known.contains(&hash) {
            report.skipped_duplicate += 1;
            continue;
        }
        let entry = ingest_file(path, store, hash)?;
        report.ingested += 1;
        report.ingested_bytes += entry.size_bytes;
        known.insert(entry.hash);
    }
    Ok(report)
}

/// Ingest one file whose content hash is already computed and known to be
/// absent from the manifest.
fn ingest_file(path: &Path, store: &Path, hash: String) -> Result<Entry> {
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
    Ok(entry)
}
