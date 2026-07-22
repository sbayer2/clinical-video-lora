//! Fixity validation: re-hash every stored file against the manifest and
//! report anything on disk the manifest does not account for. Run at end of
//! every capture session (plan section 3, "build now") and quarterly as the
//! archival scrub (ADC-007). The check log is chain-of-custody evidence.

use std::collections::HashSet;
use std::fmt;
use std::fs;
use std::path::Path;

use anyhow::{Context, Result};
use walkdir::WalkDir;

use crate::manifest;
use crate::store::{ORIGINALS_DIR, hash_file};

#[derive(Debug, Default)]
pub struct ValidateReport {
    pub ok: usize,
    /// Manifest entries whose file is absent.
    pub missing: Vec<String>,
    /// Files whose size differs from the manifest.
    pub size_mismatch: Vec<String>,
    /// Files whose content hash differs from the manifest.
    pub corrupted: Vec<String>,
    /// Files under originals/ that no manifest entry accounts for
    /// (includes leftover .partial files from interrupted ingests).
    pub orphans: Vec<String>,
}

impl ValidateReport {
    pub fn is_clean(&self) -> bool {
        self.missing.is_empty()
            && self.size_mismatch.is_empty()
            && self.corrupted.is_empty()
            && self.orphans.is_empty()
    }
}

impl fmt::Display for ValidateReport {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        writeln!(f, "verified {} file(s) ok", self.ok)?;
        for (label, paths) in [
            ("MISSING", &self.missing),
            ("SIZE MISMATCH", &self.size_mismatch),
            ("CORRUPTED", &self.corrupted),
            ("ORPHAN", &self.orphans),
        ] {
            for p in paths {
                writeln!(f, "{label}: {p}")?;
            }
        }
        if self.is_clean() {
            write!(f, "store is clean")
        } else {
            write!(
                f,
                "store is NOT clean: {} missing, {} size mismatch, {} corrupted, {} orphan(s)",
                self.missing.len(),
                self.size_mismatch.len(),
                self.corrupted.len(),
                self.orphans.len()
            )
        }
    }
}

pub fn validate_store(store: &Path) -> Result<ValidateReport> {
    let entries = manifest::load(store)?;
    let mut report = ValidateReport::default();
    let mut listed: HashSet<String> = HashSet::new();

    for entry in &entries {
        listed.insert(entry.store_path.clone());
        let path = store.join(&entry.store_path);
        if !path.is_file() {
            report.missing.push(entry.store_path.clone());
            continue;
        }
        let meta = fs::metadata(&path)
            .with_context(|| format!("cannot stat {}", path.display()))?;
        if meta.len() != entry.size_bytes {
            report.size_mismatch.push(entry.store_path.clone());
            continue;
        }
        if hash_file(&path)? != entry.hash {
            report.corrupted.push(entry.store_path.clone());
        } else {
            report.ok += 1;
        }
    }

    let originals = store.join(ORIGINALS_DIR);
    if originals.is_dir() {
        for dirent in WalkDir::new(&originals).follow_links(false).sort_by_file_name() {
            let dirent = dirent.context("cannot walk originals directory")?;
            if !dirent.file_type().is_file() {
                continue;
            }
            let rel = dirent
                .path()
                .strip_prefix(store)
                .expect("originals is under store")
                .to_string_lossy()
                .into_owned();
            if !listed.contains(&rel) {
                report.orphans.push(rel);
            }
        }
    }

    Ok(report)
}
