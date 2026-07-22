# Research: storage tiering + corpus manifest (ADC-006 / ADC-007 input)

Agent-researched 2026-07-22. Sources cited inline.

## 1. PHI video in Google Cloud Storage

### BAA: yes, self-serve, no minimum spend

- Google's standard Cloud BAA covers all of Google Cloud infrastructure plus an enumerated covered-services list including Cloud Storage, Cloud KMS, Cloud Logging ([hipaa-compliance](https://cloud.google.com/security/compliance/hipaa-compliance), [hipaa-baa terms](https://cloud.google.com/terms/hipaa-baa)).
- **Acceptance is electronic and self-serve** in the Cloud Console (IAM & Admin → Legal/Privacy → "Review and Accept" the HIPAA BAA). One acceptance covers the account. **No minimum spend, no sales contact, no org-size requirement** ([console help](https://support.google.com/cloud/answer/6329727), [totalhipaa walkthrough](https://www.totalhipaa.com/how-to-get-a-baa-with-google/)). Google will not negotiate or sign a custom BAA.
- BAA is necessary, not sufficient — shared-responsibility configuration is on the customer.
- (One fetch during research returned fabricated "$100K minimum, contact sales" claims with no page content behind them — discarded; console-help page and independent walkthroughs confirm self-serve.)

### Controls

| Control | Gives you | Note |
|---|---|---|
| CMEK (Cloud KMS) | Key custody; disabling key = crypto-shred | Covered by BAA; ~$0.06/key-version/mo |
| Bucket Lock retention | WORM; *locked* policy can never be reduced | **Caution**: locked = irrevocable even if a patient revokes consent and deletion is obligated. For a consent-driven corpus use per-object legal holds + unlocked policy + CMEK crypto-shred; reserve locked policies for audit-log buckets |
| Object holds | Per-object legal hold | Directly supports litigation hold |
| Cloud Audit Logs | Enable Data Access logs on the PHI bucket; every read/write with identity | Retain 6 years; export to a locked log bucket |
| Versioning, uniform bucket-level access, public-access prevention, VPC-SC | Standard hardening | All BAA-covered |

### Costs (US regions, 2026; [pricing](https://cloud.google.com/storage/pricing), [CloudZero guide](https://www.cloudzero.com/blog/gcp-storage-pricing/))

| Class | ≈$/TB/mo | Retrieval $/TB | Min duration |
|---|---|---|---|
| Standard | $22 | — | none |
| Nearline | $11 | $10 | 30 d |
| Coldline | $4.40 | $20 | 90 d |
| Archive | $1.23 | $50 | 365 d |

- **Egress to internet ~$120/TB dominates any full-corpus download** (restoring 50 TB from Archive to a Mac ≈ $8,500). Egress to in-region GCP compute is free — do heavy derived-artifact regeneration on GCP VMs rather than pulling originals down.
- All classes are online (millisecond first-byte, no restore jobs). Immutable never-deleted originals make Archive's 365-day minimum harmless. Steady state at 50 TB retained: ~$60–75/mo Archive, ~$220/mo Coldline. Per-clip retrieval costs cents-to-dollars.

## 2. Fully-local alternative

- 8-bay NAS (~$1,000–1,400) + 8×16 TB ⇒ ~96 TB usable SHR-2/RAID-Z2 for ~$3,300–3,800 ⇒ ~$0.6–0.8/TB/mo amortized over 5 years. Order of magnitude cheaper than Archive *if* capacity is used.
- Bit-rot protection conditional: file-data checksums must be enabled per shared folder at creation (Synology/Btrfs); scrubs must be scheduled. RAID is availability, not backup — fire/theft/ransomware/fat-finger all defeat it; the offsite-rotation leg is mandatory and is the weakest link under solo operation (manual discipline, transport risk, days-to-weeks of unprotected recent data).
- NAS internet exposure is a documented, repeatedly exploited ransomware vector (SynoLocker, DeadBolt-class). Never port-forward.

### Honest PHI comparison

- **Breach math**: stolen *unencrypted* drive of full-face video = reportable breach of every patient on it. Stolen AES-encrypted drive with keys not co-located qualifies for the **HHS safe harbor — not reportable** ([kiteworks summary](https://www.kiteworks.com/hipaa-compliance/hipaa-encryption-requirements-safe-harbor-guide/)). Full-volume encryption on NAS and rotation drives is non-negotiable.
- **Legal hold**: recordings are discoverable in malpractice litigation; notably, [PMC10917358](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10917358/) found video recording was *not* associated with increased claims, and no paid claims 2000–2017 involved recording physicians — a useful datum for the carrier conversation (plan 0.3). GCS gives per-object holds, WORM, immutable audit logs — easy to demonstrate non-tampering. On a home NAS, chain-of-custody is a manual promise; content-addressing + signed checksum manifests recover much of it.
- **Single-operator failure**: cloud corpus survives the operator's house; main cloud death-mode is billing lapse (backup payment method, documented succession).

**Bottom line: both.** Local NAS as working tier + GCS (BAA, CMEK, Coldline→Archive) as the durable immutable copy. The cloud leg replaces manual offsite rotation — precisely the leg most likely to fail solo. At 100–200 GB/hr, upload bandwidth (not cost) is the constraint — selective retention must happen *before* the cloud leg.

## 3. Manifest format: 5-year durability

| Format | 5-yr record | Rust support | Verdict |
|---|---|---|---|
| Content-addressed files + JSONL + SQLite | Perfect (JSON RFC 8259; SQLite on-disk format unchanged since 2004, Library of Congress preservation format) | Excellent (`serde_json`, `rusqlite`, `blake3`, `object_store`) | **Recommended** |
| Parquet | Excellent, decade-stable, multi-vendor | Excellent (`parquet`/`arrow-rs` is the official Apache impl) | For tabular annotation data *if* it ever gets large; one-liner conversion from JSONL |
| WebDataset | Container immortal; convention PyTorch-centric, single-maintainer-ish | No first-class crate | Streaming-training optimization irrelevant at thousands of large clips; tar hides clips from ordinary tools |
| Lance | Format 2.1 declared stable only in 2025; 3 format iterations in ~2 years; single-vendor | Native (it is Rust) | Solves problems this corpus doesn't have |
| HF `datasets` | Poor: v4.0 removed loading scripts, broke cached datasets ([#7676](https://github.com/huggingface/datasets/issues/7676)) | None | A loader, not an archival format; Hub workflows unusable for PHI |

**Does columnar earn its complexity at ~thousands of clips? No.** 5,000 clips × ~1 KB metadata = ~5 MB — SQLite or grep scans in milliseconds; columnar wins start at millions of rows or tiny samples. Every training architecture of the next 5 years accepts "file paths + labels"; not every one reads Lance 2.1. **Format-neutrality is architecture-neutrality**: originals as plain media files; generate WebDataset/HF/Lance/Parquet views on demand as disposable derived artifacts (plan section 5's design rule, confirmed).

**Recommended shape**: `originals/<hash-prefix>/<hash>.mov` (BLAKE3 content-addressing: immutability, dedup, integrity, location-independence across NAS and GCS) + append-only JSONL manifest (hash, timestamps, encounter id, consent reference, device, codec params, size, duration) as ground truth + SQLite for annotations/queries, rebuildable from JSONL.

## 4. Provenance

**JSON sidecars** (`<artifact>.prov.json`: source hashes, tool name+version, exact parameters, timestamp, output hash), written by the harness at generation time, harness itself in git so tool version = git tag. Zero infrastructure; interpretable in 2031 with nothing installed. DVC redundant (its cache *is* content-addressing you already built); git-annex idiosyncratic; lakeFS team-scale overkill.

## 5. Archival integrity

- **BagIt (RFC 8493)**-style `manifest-sha256.txt` per acquisition batch — costs nothing, standard fixity tooling compatibility.
- **Scrub, three layers**: monthly ZFS/Btrfs scrub; quarterly harness fixity sweep re-hashing against manifest; cloud-side hash-vs-CRC32C verification without egress. **Log every fixity check — that log is chain-of-custody evidence.**
- **3-2-1**: NAS working + GCS Coldline/Archive + optional third leg (Backblaze B2 with BAA, ~$6/TB/mo, or weekly-rotated encrypted drive for the tiny manifest/annotation DB).
- **Client-side encryption does NOT remove the BAA requirement**: HHS is explicit that a CSP storing ePHI is a business associate even for no-view services — "lacking an encryption key does not exempt a CSP" ([HHS cloud guidance](https://www.hhs.gov/hipaa/for-professionals/special-topics/health-information-technology/cloud-computing/index.html)). Client-side encryption still worth doing — it changes breach math (safe harbor), not BA status. Every cloud leg needs a BAA; both realistic candidates (GCS self-serve, B2 on request) make that easy.

## Recommendations

**(a) Tiering**: local NAS (encrypted, checksummed, scrubbed) as capture/working tier where selective retention happens → GCS under self-serve BAA, CMEK, Coldline with lifecycle to Archive, per-object holds, Data Access logging to a locked log bucket → optional B2 third leg. Regenerate derivations on in-region GCP compute rather than egressing originals.

**(b) Manifest**: the boring option, deliberately — content-addressed native media files + append-only JSONL + SQLite + provenance sidecars + BagIt-style batch manifests; loader formats emitted on demand and treated as disposable.
