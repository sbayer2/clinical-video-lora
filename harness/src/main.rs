use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(
    name = "harness",
    version,
    about = "Encounter session store: immutable content-addressed ingest and fixity validation"
)]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// End-of-session capture check: decode-verify audio, parse video
    /// containers, and confirm durations agree; run before room teardown
    Check {
        /// Capture directory to verify
        dir: PathBuf,
        /// Expected audio sample rate in Hz
        #[arg(long, default_value_t = 48_000)]
        expect_sample_rate: u32,
        /// Minimum expected audio bit depth
        #[arg(long, default_value_t = 24)]
        expect_bit_depth: u32,
        /// Maximum allowed duration spread across files, in seconds
        #[arg(long, default_value_t = 1.0)]
        sync_tolerance_secs: f64,
    },
    /// Ingest media files from a directory into the immutable session store
    Ingest {
        /// Directory containing capture output to ingest
        src: PathBuf,
        /// Store root (created if absent)
        #[arg(long)]
        store: PathBuf,
    },
    /// Re-hash every stored file against the manifest; nonzero exit if anything is wrong
    Validate {
        /// Store root
        #[arg(long)]
        store: PathBuf,
    },
}

fn main() -> ExitCode {
    match run() {
        Ok(code) => code,
        Err(err) => {
            eprintln!("error: {err:#}");
            ExitCode::from(2)
        }
    }
}

fn run() -> anyhow::Result<ExitCode> {
    match Cli::parse().cmd {
        Cmd::Check { dir, expect_sample_rate, expect_bit_depth, sync_tolerance_secs } => {
            let expect = harness::media::Expectations {
                sample_rate: expect_sample_rate,
                bits_per_sample: expect_bit_depth,
                sync_tolerance_secs,
            };
            let report = harness::media::check_dir(&dir, &expect)?;
            println!("{report}");
            Ok(if report.is_clean() {
                ExitCode::SUCCESS
            } else {
                ExitCode::FAILURE
            })
        }
        Cmd::Ingest { src, store } => {
            let report = harness::store::ingest_dir(&src, &store)?;
            println!("{report}");
            Ok(ExitCode::SUCCESS)
        }
        Cmd::Validate { store } => {
            let report = harness::validate::validate_store(&store)?;
            println!("{report}");
            Ok(if report.is_clean() {
                ExitCode::SUCCESS
            } else {
                ExitCode::FAILURE
            })
        }
    }
}
