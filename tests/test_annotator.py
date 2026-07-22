"""Annotator server tests: token gate, queue, schema validation on save,
media path safety, Range serving, export."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from annotator.main import create_app  # noqa: E402

TOKEN = "test-token"


def valid_record() -> dict:
    return {
        "schema_version": "0.1.0",
        "record_id": "6f9619ff-8b86-4d01-b42d-00cf4fc964ff",
        "session_id": "clipA",
        "capture": "recording",
        "clip": {"t_start": 1.0, "t_end": 4.5},
        "segment_class": "reassurance",
        "read": {
            "acuity": "low",
            "affect_observed": "frightened",
            "prior_relationship": "none",
            "register_selected": "warm",
            "why": "first-time parent, fear disproportionate to findings",
        },
        "move": {
            "description": "slowed down, named the fear before the findings",
            "discriminating_feature": "fear was of the diagnosis name, not the symptoms",
        },
        "self_rating": {"worked": 1, "confidence": 0.8},
        "annotated_at": "2026-07-22T22:00:00Z",
    }


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    clips = tmp_path / "clips"
    clips.mkdir()
    (clips / "clipA.mp4").write_bytes(b"\x00" * 4096)
    (clips / "clipB.mov").write_bytes(b"\x01" * 4096)
    (clips / "notes.txt").write_text("not media")
    app = create_app(clips, tmp_path / "annotations.db", TOKEN)
    return TestClient(app)


def get(client: TestClient, path: str, **kw):
    joiner = "&" if "?" in path else "?"
    return client.get(f"{path}{joiner}token={TOKEN}", **kw)


def test_api_requires_token(client: TestClient):
    assert client.get("/api/queue").status_code == 401
    assert client.get("/api/queue?token=wrong").status_code == 401
    assert get(client, "/api/queue").status_code == 200


def test_queue_lists_media_only(client: TestClient):
    queue = get(client, "/api/queue").json()
    assert [q["file"] for q in queue] == ["clipA.mp4", "clipB.mov"]
    assert all(q["records"] == 0 for q in queue)


def test_media_supports_range_and_blocks_traversal(client: TestClient):
    res = get(client, "/api/media/clipA.mp4", headers={"Range": "bytes=0-99"})
    assert res.status_code == 206, "video seeking depends on Range support"
    assert len(res.content) == 100
    assert client.get(f"/api/media/..%2Fannotations.db?token={TOKEN}").status_code in (400, 404)
    assert get(client, "/api/media/notes.txt").status_code == 404


def test_save_valid_record_marks_clip_annotated(client: TestClient):
    res = client.post(
        f"/api/records?token={TOKEN}",
        json={"clip_file": "clipA.mp4", "record": valid_record()},
    )
    assert res.status_code == 200, res.text
    queue = {q["file"]: q["records"] for q in get(client, "/api/queue").json()}
    assert queue["clipA.mp4"] == 1
    assert queue["clipB.mov"] == 0
    export = get(client, "/api/export").text
    assert "6f9619ff-8b86-4d01-b42d-00cf4fc964ff" in export
    records = get(client, "/api/records/clipA.mp4").json()
    assert records[0]["read"]["register_selected"] == "warm"


def test_schema_rejects_bad_records(client: TestClient):
    missing_why = valid_record()
    del missing_why["read"]["why"]
    res = client.post(
        f"/api/records?token={TOKEN}",
        json={"clip_file": "clipA.mp4", "record": missing_why},
    )
    assert res.status_code == 422
    assert any("why" in d["message"] for d in res.json()["detail"])

    extra_field = valid_record()
    extra_field["not_in_schema"] = True
    res = client.post(
        f"/api/records?token={TOKEN}",
        json={"clip_file": "clipA.mp4", "record": extra_field},
    )
    assert res.status_code == 422

    recording_without_clip = valid_record()
    del recording_without_clip["clip"]
    res = client.post(
        f"/api/records?token={TOKEN}",
        json={"clip_file": "clipA.mp4", "record": recording_without_clip},
    )
    assert res.status_code == 422


def test_save_rejects_unknown_clip(client: TestClient):
    res = client.post(
        f"/api/records?token={TOKEN}",
        json={"clip_file": "ghost.mp4", "record": valid_record()},
    )
    assert res.status_code == 404
