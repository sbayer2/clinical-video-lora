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
