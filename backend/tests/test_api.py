import struct
from pathlib import Path

from fastapi.testclient import TestClient

from kivi_memory.api import create_app
from kivi_memory.db import Embedding, build_session_factory
from kivi_memory.settings import Settings


def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(Settings(app_data_dir=tmp_path)))


def test_import_process_and_answer(tmp_path: Path):
    app = client(tmp_path)
    namespace = app.post("/api/namespaces", json={"name": "Test"}).json()
    record = '{"schema_version":1,"id":"lantern","raw_asr":"lantern launch monday not friday","formatted_text":"Lantern launches Monday, not Friday."}'
    imported = app.post(f"/api/namespaces/{namespace['id']}/imports", json={"jsonl": record})
    assert imported.status_code == 200
    assert imported.json()["accepted"] == 1
    assert app.post("/api/worker/run-once").json()["state"] == "completed"
    result = app.post(f"/api/namespaces/{namespace['id']}/ask", json={"question": "When does Lantern launch?"})
    assert result.status_code == 200
    assert result.json()["status"] == "answered"
    assert result.json()["evidence"][0]["external_id"] == "lantern"


def test_unknown_history_refuses_to_speculate(tmp_path: Path):
    app = client(tmp_path)
    namespace = app.post("/api/namespaces", json={"name": "Unknown"}).json()
    record = '{"schema_version":1,"id":"lantern","raw_asr":"lantern launch monday","formatted_text":"Lantern launches Monday."}'
    app.post(f"/api/namespaces/{namespace['id']}/imports", json={"jsonl": record})
    app.post("/api/worker/run-once")
    result = app.post(f"/api/namespaces/{namespace['id']}/ask", json={"question": "Which orbital telescope needs a xenon refill?"})
    assert result.json()["status"] == "insufficient_evidence"


def test_invalid_import_is_visible(tmp_path: Path):
    app = client(tmp_path)
    namespace = app.post("/api/namespaces", json={"name": "Test"}).json()
    result = app.post(f"/api/namespaces/{namespace['id']}/imports/validate", json={"jsonl": "not json"})
    assert result.status_code == 200
    assert result.json()["invalid_count"] == 1


def test_only_failed_jobs_can_be_retried(tmp_path: Path):
    app = client(tmp_path)
    namespace = app.post("/api/namespaces", json={"name": "Retry"}).json()
    record = '{"schema_version":1,"id":"one","raw_asr":"one","formatted_text":"One."}'
    imported = app.post(f"/api/namespaces/{namespace['id']}/imports", json={"jsonl": record}).json()
    job_id = imported["job_ids"][0]
    result = app.post(f"/api/namespaces/{namespace['id']}/jobs/{job_id}/retry")
    assert result.status_code == 409


def test_suppression_needs_current_revision(tmp_path: Path):
    app = client(tmp_path)
    namespace = app.post("/api/namespaces", json={"name": "Controls"}).json()
    record = '{"schema_version":1,"id":"pref","raw_asr":"i prefer concise emails","formatted_text":"I prefer concise emails."}'
    app.post(f"/api/namespaces/{namespace['id']}/imports", json={"jsonl": record})
    app.post("/api/worker/run-once")
    current = app.get("/api/namespaces").json()[0]
    memory = app.get(f"/api/namespaces/{namespace['id']}/memories").json()[0]
    result = app.post(f"/api/namespaces/{namespace['id']}/memories/{memory['id']}/suppressions", json={"expected_revision": current["revision"], "operation_id": "operation-123"})
    assert result.status_code == 200
    assert result.json()["state"] == "suppressed"


def test_worker_persists_vectors_when_an_encoder_is_available(tmp_path: Path, monkeypatch):
    # The real model is covered by the disposable CLI rehearsal.  This test keeps
    # the parent-chunk/embedding transaction ordering under regression coverage.
    monkeypatch.setattr("kivi_memory.services.encode_passage", lambda _settings, _text: (struct.pack("<ff", 0.6, 0.8), 2))
    app = client(tmp_path)
    namespace = app.post("/api/namespaces", json={"name": "Vectors"}).json()
    record = '{"schema_version":1,"id":"vector","raw_asr":"lantern launch monday","formatted_text":"Lantern launches Monday."}'
    app.post(f"/api/namespaces/{namespace['id']}/imports", json={"jsonl": record})
    assert app.post("/api/worker/run-once").json()["state"] == "completed"
    factory, _ = build_session_factory(Settings(app_data_dir=tmp_path))
    with factory() as session:
        assert session.query(Embedding).count() == 2
