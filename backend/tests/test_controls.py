from pathlib import Path

from fastapi.testclient import TestClient

from kivi_memory.api import create_app
from kivi_memory.settings import Settings


def test_source_delete_removes_it_from_future_answers(tmp_path: Path):
    app = TestClient(create_app(Settings(app_data_dir=tmp_path, sarvam_api_key="")))
    namespace = app.post("/api/namespaces", json={"name": "Delete"}).json()
    record = '{"schema_version":1,"id":"one","raw_asr":"lantern launches monday","formatted_text":"Lantern launches Monday."}'
    app.post(f"/api/namespaces/{namespace['id']}/imports", json={"jsonl": record})
    app.post("/api/worker/run-once")
    current = app.get("/api/namespaces").json()[0]
    source = app.get(f"/api/namespaces/{namespace['id']}/sources").json()[0]
    deleted = app.delete(f"/api/namespaces/{namespace['id']}/sources/{source['id']}?expected_revision={current['revision']}&operation_id=delete-operation-123")
    assert deleted.status_code == 200
    answer = app.post(f"/api/namespaces/{namespace['id']}/ask", json={"question": "When does Lantern launch?"})
    assert answer.json()["status"] == "insufficient_evidence"


def test_correction_is_idempotent_and_inspectable(tmp_path: Path):
    app = TestClient(create_app(Settings(app_data_dir=tmp_path, sarvam_api_key="")))
    namespace = app.post("/api/namespaces", json={"name": "Correct"}).json()
    record = '{"schema_version":1,"id":"pref","raw_asr":"i prefer short emails","formatted_text":"I prefer short emails."}'
    app.post(f"/api/namespaces/{namespace['id']}/imports", json={"jsonl": record})
    app.post("/api/worker/run-once")
    memory = app.get(f"/api/namespaces/{namespace['id']}/memories").json()[0]
    revision = app.get("/api/namespaces").json()[0]["revision"]
    payload = {"value": "concise emails with a clear decision", "expected_revision": revision, "operation_id": "correction-operation-123"}
    first = app.post(f"/api/namespaces/{namespace['id']}/memories/{memory['id']}/corrections", json=payload)
    repeated = app.post(f"/api/namespaces/{namespace['id']}/memories/{memory['id']}/corrections", json=payload)
    operations = app.get(f"/api/namespaces/{namespace['id']}/operations").json()
    assert first.status_code == 200
    assert repeated.json()["state"] == "already_applied"
    assert operations[0]["kind"] == "correct"

    answer = app.post(
        f"/api/namespaces/{namespace['id']}/ask",
        json={"question": "What email style do I prefer?"},
    ).json()
    assert answer["generation"] == "controlled_memory"
    assert "concise emails with a clear decision" in answer["answer"]
    assert answer["claims"][0]["kind"] == "user_corrected_memory"


def test_suppressed_memory_is_not_replayed_as_an_answer(tmp_path: Path):
    app = TestClient(create_app(Settings(app_data_dir=tmp_path, sarvam_api_key="")))
    namespace = app.post("/api/namespaces", json={"name": "Suppress"}).json()
    record = '{"schema_version":1,"id":"pref","raw_asr":"i prefer short emails","formatted_text":"I prefer short emails."}'
    app.post(f"/api/namespaces/{namespace['id']}/imports", json={"jsonl": record})
    app.post("/api/worker/run-once")
    memory = app.get(f"/api/namespaces/{namespace['id']}/memories").json()[0]
    revision = app.get("/api/namespaces").json()[0]["revision"]
    app.post(
        f"/api/namespaces/{namespace['id']}/memories/{memory['id']}/suppressions",
        json={"expected_revision": revision, "operation_id": "suppress-operation-123"},
    )
    answer = app.post(
        f"/api/namespaces/{namespace['id']}/ask",
        json={"question": "What email style do I prefer?"},
    ).json()
    assert answer["status"] == "insufficient_evidence"


def test_operation_id_cannot_be_reused_for_a_different_change(tmp_path: Path):
    app = TestClient(create_app(Settings(app_data_dir=tmp_path, sarvam_api_key="")))
    namespace = app.post("/api/namespaces", json={"name": "Operations"}).json()
    record = '{"schema_version":1,"id":"pref","raw_asr":"i prefer short emails","formatted_text":"I prefer short emails."}'
    app.post(f"/api/namespaces/{namespace['id']}/imports", json={"jsonl": record})
    app.post("/api/worker/run-once")
    memory = app.get(f"/api/namespaces/{namespace['id']}/memories").json()[0]
    source = app.get(f"/api/namespaces/{namespace['id']}/sources").json()[0]
    revision = app.get("/api/namespaces").json()[0]["revision"]
    operation_id = "one-operation-id"
    applied = app.post(
        f"/api/namespaces/{namespace['id']}/memories/{memory['id']}/suppressions",
        json={"expected_revision": revision, "operation_id": operation_id},
    )
    reused = app.delete(
        f"/api/namespaces/{namespace['id']}/sources/{source['id']}",
        params={"expected_revision": applied.json()["revision"], "operation_id": operation_id},
    )
    assert reused.status_code == 409
    assert reused.json()["code"] == "operation_id_reused"
