"""Build the blind-annotation queue for the agreement experiment (ADC-012).

Reads the frozen machine records, cuts each annotated window out of its
source film into an anonymously named clip (deterministic shuffle), and
writes a manifest mapping blind names back to (film, window, machine
record). The annotator sees only blind_NN.mp4 files — no film names in the
queue, no machine clip bounds, no machine record content. The manifest is
the join key for scripts/agreement.py afterward; don't read it while
annotating.

Run:  .venv/bin/python scripts/make_blind_queue.py
Then: .venv/bin/python -m annotator.main --clips scripts/out/blind_queue
"""

import json
import random
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ANNOT_DIR = REPO_ROOT / "scripts" / "out" / "ai_annotations"
MEDIA_DIR = REPO_ROOT / "media" / "archive"
QUEUE_DIR = REPO_ROOT / "scripts" / "out" / "blind_queue"
SHUFFLE_SEED = 20260723


def main() -> None:
    windows = []
    for jl in sorted(ANNOT_DIR.glob("*.jsonl")):
        if ".qwen" in jl.name:  # local-model records join later via same manifest
            continue
        for line in jl.read_text().splitlines():
            w = json.loads(line)
            windows.append({
                "source_file": w["source_file"],
                "t0": w["window"]["t0"],
                "t1": w["window"]["t1"],
                "machine_record_file": jl.name,
                "machine_record_id": w["record"]["record_id"],
            })
    if not windows:
        raise SystemExit(f"no machine records found in {ANNOT_DIR}")

    random.Random(SHUFFLE_SEED).shuffle(windows)
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)

    manifest = {}
    for i, w in enumerate(windows, 1):
        blind = f"blind_{i:02d}"
        src = MEDIA_DIR / w["source_file"]
        dest = QUEUE_DIR / f"{blind}.mp4"
        if not dest.exists():
            subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-ss", str(w["t0"]), "-t", str(w["t1"] - w["t0"]), "-i", str(src),
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-c:a", "aac", "-movflags", "+faststart", str(dest)],
                check=True,
            )
        manifest[blind] = w
        print(f"{blind}.mp4  <-  {w['source_file']} [{w['t0']:.0f}-{w['t1']:.0f}s]")

    (QUEUE_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\n{len(manifest)} blind clips in {QUEUE_DIR.relative_to(REPO_ROOT)}")
    print("annotate with: .venv/bin/python -m annotator.main --clips scripts/out/blind_queue")


if __name__ == "__main__":
    main()
