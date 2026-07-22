"""Annotation UI server (ADC-005, ADC-011): clip-review loop over a
directory of composed proxies.

Loopback-only with a per-run token gating /api/* — the media is PHI, the
static shell is not. Records are validated server-side against the
canonical JSON Schema before touching the database.

Run:  .venv/bin/python -m annotator.main --clips <proxy-dir>
"""

import argparse
import json
import secrets
import time
from pathlib import Path

import jsonschema
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import db

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "schema" / "encounter_record.schema.json"
STATIC_DIR = Path(__file__).resolve().parent / "static"
MEDIA_EXTS = {".mp4", ".mov", ".m4v"}


def create_app(clips_dir: Path, db_path: Path, token: str) -> FastAPI:
    schema = json.loads(SCHEMA_PATH.read_text())
    validator = jsonschema.Draft202012Validator(schema)
    app = FastAPI()

    @app.middleware("http")
    async def gate(request: Request, call_next):
        if request.url.path.startswith("/api"):
            supplied = request.query_params.get("token") or request.headers.get("x-token")
            if supplied != token:
                return JSONResponse({"detail": "bad or missing token"}, status_code=401)
        return await call_next(request)

    def clip_path(name: str) -> Path:
        # Basename-only lookup; anything path-like is rejected outright.
        if name != Path(name).name:
            raise HTTPException(400, "invalid clip name")
        path = clips_dir / name
        if not path.is_file() or path.suffix.lower() not in MEDIA_EXTS:
            raise HTTPException(404, f"no such clip: {name}")
        return path

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/queue")
    async def queue():
        counts = db.counts_by_clip(db_path)
        files = sorted(
            p.name for p in clips_dir.iterdir()
            if p.is_file() and p.suffix.lower() in MEDIA_EXTS
        )
        return [{"file": f, "records": counts.get(f, 0)} for f in files]

    @app.get("/api/schema")
    async def get_schema():
        return schema

    @app.get("/api/media/{name}")
    async def media(name: str):
        # Starlette's FileResponse handles HTTP Range requests, which the
        # <video> element depends on for seeking.
        return FileResponse(clip_path(name))

    @app.get("/api/records/{name}")
    async def records(name: str):
        return db.records_for_clip(db_path, clip_path(name).name)

    @app.post("/api/records")
    async def save(payload: dict):
        clip_file = payload.get("clip_file")
        record = payload.get("record")
        if not isinstance(clip_file, str) or not isinstance(record, dict):
            raise HTTPException(422, "payload must be {clip_file, record}")
        clip_path(clip_file)
        errors = sorted(validator.iter_errors(record), key=lambda e: list(e.absolute_path))
        if errors:
            detail = [
                {"path": "/".join(str(p) for p in e.absolute_path), "message": e.message}
                for e in errors
            ]
            raise HTTPException(422, detail)
        created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        db.insert_record(db_path, clip_file, record, created_at)
        return {"saved": record["record_id"]}

    @app.get("/api/export")
    async def export():
        return PlainTextResponse(db.export_jsonl(db_path), media_type="application/jsonl")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Encounter annotation UI")
    parser.add_argument("--clips", type=Path, required=True, help="directory of proxy clips")
    parser.add_argument("--db", type=Path, default=None, help="SQLite path (default: <clips>/annotations.db)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    clips_dir = args.clips.resolve()
    if not clips_dir.is_dir():
        raise SystemExit(f"not a directory: {clips_dir}")
    db_path = args.db or clips_dir / "annotations.db"
    token = secrets.token_urlsafe(16)
    app = create_app(clips_dir, db_path, token)
    print(f"\n  http://127.0.0.1:{args.port}/?token={token}\n")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
