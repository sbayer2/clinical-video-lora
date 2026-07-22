"""SQLite record store for the annotation UI.

The record JSON stays schema-pure (validated against
schema/encounter_record.schema.json, additionalProperties: false); anything
the UI needs beyond the schema — which proxy file the record came from —
lives in side columns, never inside the record.
"""

import json
import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS records (
    record_id     TEXT PRIMARY KEY,
    clip_file     TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    segment_class TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    json          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_clip ON records (clip_file);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    return conn


def insert_record(db_path: Path, clip_file: str, record: dict, created_at: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO records (record_id, clip_file, session_id, segment_class, created_at, json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                record["record_id"],
                clip_file,
                record["session_id"],
                record["segment_class"],
                created_at,
                json.dumps(record, ensure_ascii=False),
            ),
        )


def counts_by_clip(db_path: Path) -> dict[str, int]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT clip_file, COUNT(*) FROM records GROUP BY clip_file"
        ).fetchall()
    return dict(rows)


def records_for_clip(db_path: Path, clip_file: str) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT json FROM records WHERE clip_file = ? ORDER BY created_at",
            (clip_file,),
        ).fetchall()
    return [json.loads(r[0]) for r in rows]


def export_jsonl(db_path: Path) -> str:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT json FROM records ORDER BY created_at").fetchall()
    return "\n".join(r[0] for r in rows) + ("\n" if rows else "")
