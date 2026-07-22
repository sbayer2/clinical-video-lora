//! Append-only JSONL manifest — the ground truth of the session store (ADC-007).

use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

pub const MANIFEST_FILE: &str = "manifest.jsonl";
pub const MANIFEST_VERSION: &str = "0.1.0";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Entry {
    pub manifest_version: String,
    /// BLAKE3 hex digest of the file contents; also the store filename stem.
    pub hash: String,
    /// Path relative to the store root, e.g. `originals/ab/ab12....mov`.
    pub store_path: String,
    pub original_name: String,
    pub size_bytes: u64,
    /// RFC 3339 UTC.
    pub ingested_at: String,
    pub tool: Tool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Tool {
    pub name: String,
    pub version: String,
}

impl Tool {
    pub fn this() -> Self {
        Tool {
            name: env!("CARGO_PKG_NAME").to_string(),
            version: env!("CARGO_PKG_VERSION").to_string(),
        }
    }
}

pub fn manifest_path(store: &Path) -> PathBuf {
    store.join(MANIFEST_FILE)
}

/// Load all entries. A missing manifest is an empty store, not an error.
/// A malformed line is an error: the manifest is ground truth and must
/// never be silently partially read.
pub fn load(store: &Path) -> Result<Vec<Entry>> {
    let path = manifest_path(store);
    if !path.exists() {
        return Ok(Vec::new());
    }
    let file =
        File::open(&path).with_context(|| format!("cannot open manifest {}", path.display()))?;
    let mut entries = Vec::new();
    for (idx, line) in BufReader::new(file).lines().enumerate() {
        let line = line.with_context(|| format!("cannot read manifest line {}", idx + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        let entry: Entry = serde_json::from_str(&line)
            .with_context(|| format!("malformed manifest line {}", idx + 1))?;
        entries.push(entry);
    }
    Ok(entries)
}

pub fn append(store: &Path, entry: &Entry) -> Result<()> {
    let path = manifest_path(store);
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .with_context(|| format!("cannot open manifest {} for append", path.display()))?;
    let mut line = serde_json::to_string(entry).context("cannot serialize manifest entry")?;
    line.push('\n');
    file.write_all(line.as_bytes())
        .context("cannot write manifest entry")?;
    file.sync_all().context("cannot sync manifest to disk")?;
    Ok(())
}
