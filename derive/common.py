"""Shared machinery for annotation stages (cloud and local).

Deliberately dependency-light: stdlib + jsonschema only, so it imports
cleanly in both the project venv (anthropic/mlx-whisper stack) and the
mlx-vlm venv used for local models.
"""

import copy
import hashlib
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "schema" / "encounter_record.schema.json"
OUT_DIR = REPO_ROOT / "scripts" / "out" / "ai_annotations"
CACHE_DIR = OUT_DIR / "cache"

FRAMES_PER_WINDOW = 4
FRAME_WIDTH = 640

SYSTEM_PROMPT = """\
You are annotating clinical encounter footage for a research corpus on
clinician communication ("delivery conditioned on clinical read"). You will
see sampled frames, a transcript slice, and paralinguistic measurements from
one window of a longer film.

Your task: select the single most annotation-worthy communication moment in
this window and describe it as one record following the provided JSON schema.
Definitions:
- "read": what the clinician appeared to be perceiving at the moment
  (patient acuity, observable affect) and the delivery register they selected
  in response.
- "read.why": what in the patient's presentation drove that register choice.
- "move.description": what the clinician did communicatively and what they
  appeared to be going for.
- "move.discriminating_feature": what made this the right call rather than a
  plausible alternative.
- "in_frame_response": observable patient shift after the move, if any.
- "self_rating.worked": whether the move visibly worked (0 or 1);
  "confidence" in [0,1] — be honest, you are inferring from tape.
- "schema_gap": what mattered in this moment that the schema fields could
  NOT capture. Write "nothing" only if true.

Constraints:
- clip.t_start and clip.t_end are in seconds on the film's own clock and
  must lie within this window's bounds; keep the clip tight around the
  moment (typically 10-90 seconds).
- capture is "recording". session_id is given to you. Omit record_id and
  annotated_at; they are stamped by the pipeline.
- context must be de-identified: no names, no dates, no facility names.
- You are annotating APPEARANCES on tape, not a clinician's inner state.
  Where the schema asks for the read, report the read as evidenced by
  observable behavior, and let your confidence reflect that limit.
- Prefer non-routine moments (de-escalation, reassurance, bad-news,
  refusal-conversion, instruction-delivery) over routine when present.

Respond with the JSON record only.\
"""


def sh(args: list[str]) -> bytes:
    return subprocess.run(args, check=True, capture_output=True).stdout


def media_duration(path: Path) -> float:
    out = sh(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)])
    return float(json.loads(out)["format"]["duration"])


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_frame_jpegs(path: Path, t0: float, t1: float) -> list[tuple[float, bytes]]:
    """Evenly sampled JPEG frames as (timestamp, jpeg bytes)."""
    frames = []
    for i in range(FRAMES_PER_WINDOW):
        t = t0 + (t1 - t0) * (i + 0.5) / FRAMES_PER_WINDOW
        jpg = sh(["ffmpeg", "-hide_banner", "-loglevel", "error",
                  "-ss", str(t), "-i", str(path), "-frames:v", "1",
                  "-vf", f"scale={FRAME_WIDTH}:-2", "-f", "image2", "-c:v", "mjpeg", "-"])
        frames.append((round(t, 1), jpg))
    return frames


def extract_audio_window(path: Path, t0: float, t1: float, dest: Path) -> Path:
    """Window audio as 16 kHz mono WAV (the native input for omni models)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    sh(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", str(t0), "-t", str(t1 - t0), "-i", str(path),
        "-ac", "1", "-ar", "16000", str(dest)])
    return dest


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def sanitized_schema(schema: dict) -> dict:
    """Copy of the canonical schema with keywords constrained decoders
    reject (minLength, if/then allOf) stripped. Canonical validation still
    runs afterward — this only shapes decoding."""
    s = copy.deepcopy(schema)

    def strip(node):
        if isinstance(node, dict):
            node.pop("minLength", None)
            node.pop("allOf", None)
            node.pop("$schema", None)
            node.pop("$id", None)
            for v in node.values():
                strip(v)
        elif isinstance(node, list):
            for v in node:
                strip(v)

    strip(s)
    return s


def prompt_sha() -> str:
    return hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:16]


def wrap_record(record: dict, *, model: str, tool: str, source: Path,
                source_hash: str, t0: float, t1: float) -> dict:
    """Machine-annotation provenance wrapper (ADC-012): unmixable from
    human records by construction."""
    return {
        "annotator": "machine",
        "model": model,
        "prompt_sha256": prompt_sha(),
        "tool": {"name": tool, "version": "0.1.0"},
        "source_file": source.name,
        "source_sha256": source_hash,
        "window": {"t0": round(t0, 1), "t1": round(t1, 1)},
        "record": record,
    }


def extract_json(text: str) -> dict:
    """Parse a JSON object from model output that may carry code fences or
    surrounding prose."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.startswith("json") else text
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON object in output: {text[:120]!r}")
    return json.loads(text[start : end + 1])
