import json
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


def test_blank_correction_is_rejected(tmp_path: Path):
    app = TestClient(create_app(Settings(app_data_dir=tmp_path, sarvam_api_key="")))
    namespace = app.post("/api/namespaces", json={"name": "BlankTest"}).json()
    record = '{"schema_version":1,"id":"pref","raw_asr":"i prefer tea","formatted_text":"I prefer tea."}'
    app.post(f"/api/namespaces/{namespace['id']}/imports", json={"jsonl": record})
    app.post("/api/worker/run-once")
    memory = app.get(f"/api/namespaces/{namespace['id']}/memories").json()[0]
    revision = app.get("/api/namespaces").json()[0]["revision"]

    # Whitespace-only string must fail with 422
    res = app.post(
        f"/api/namespaces/{namespace['id']}/memories/{memory['id']}/corrections",
        json={"value": "   ", "expected_revision": revision, "operation_id": "blank-correction-123"},
    )
    assert res.status_code == 422


def test_correcting_superseded_memory_returns_409(tmp_path: Path):
    app = TestClient(create_app(Settings(app_data_dir=tmp_path, sarvam_api_key="")))
    namespace = app.post("/api/namespaces", json={"name": "Superseded"}).json()
    record = '{"schema_version":1,"id":"pref","raw_asr":"i prefer tea","formatted_text":"I prefer tea."}'
    app.post(f"/api/namespaces/{namespace['id']}/imports", json={"jsonl": record})
    app.post("/api/worker/run-once")
    memory = app.get(f"/api/namespaces/{namespace['id']}/memories").json()[0]
    revision = app.get("/api/namespaces").json()[0]["revision"]

    # First correction succeeds
    corr1 = app.post(
        f"/api/namespaces/{namespace['id']}/memories/{memory['id']}/corrections",
        json={"value": "coffee", "expected_revision": revision, "operation_id": "corr-1-12345678"},
    )
    assert corr1.status_code == 200

    # Attempting to correct the original memory again must fail because it is now superseded
    next_rev = corr1.json()["revision"]
    corr2 = app.post(
        f"/api/namespaces/{namespace['id']}/memories/{memory['id']}/corrections",
        json={"value": "water", "expected_revision": next_rev, "operation_id": "corr-2-12345678"},
    )
    assert corr2.status_code == 409
    assert corr2.json()["code"] == "memory_not_active"


def test_temporal_ordering_respects_occurred_at(tmp_path: Path):
    app = TestClient(create_app(Settings(app_data_dir=tmp_path, sarvam_api_key="")))
    namespace = app.post("/api/namespaces", json={"name": "Temporal"}).json()

    # Newer record imported first
    rec_newer = (
        '{"schema_version":1,"id":"rec-newer","raw_asr":"lantern launch wednesday",'
        '"formatted_text":"Lantern launches Wednesday.","occurred_at":"2026-09-09T12:00:00Z"}'
    )
    app.post(f"/api/namespaces/{namespace['id']}/imports", json={"jsonl": rec_newer})
    app.post("/api/worker/run-once")

    # Older record imported later
    rec_older = (
        '{"schema_version":1,"id":"rec-older","raw_asr":"lantern launch monday",'
        '"formatted_text":"Lantern launches Monday.","occurred_at":"2026-09-01T12:00:00Z"}'
    )
    app.post(f"/api/namespaces/{namespace['id']}/imports", json={"jsonl": rec_older})
    app.post("/api/worker/run-once")

    memories = app.get(f"/api/namespaces/{namespace['id']}/memories").json()
    active_mems = [m for m in memories if m["state"] == "active"]
    assert len(active_mems) == 1
    # The active memory must be Wednesday, not the older Monday record
    assert active_mems[0]["value"] == "Wednesday"


def test_deleted_source_text_is_redacted_from_query_history(tmp_path: Path):
    app = TestClient(create_app(Settings(app_data_dir=tmp_path, sarvam_api_key="")))
    namespace = app.post("/api/namespaces", json={"name": "Redaction"}).json()
    record = '{"schema_version":1,"id":"apollo","raw_asr":"apollo confidential test","formatted_text":"Apollo confidential test data."}'
    app.post(f"/api/namespaces/{namespace['id']}/imports", json={"jsonl": record})
    app.post("/api/worker/run-once")

    # Perform a query that cites the secret record
    ask_res = app.post(f"/api/namespaces/{namespace['id']}/ask", json={"question": "What is Apollo?"}).json()
    assert ask_res["status"] == "answered"
    trace_id = ask_res["trace_id"]

    # Delete the source
    source = app.get(f"/api/namespaces/{namespace['id']}/sources").json()[0]
    rev = app.get("/api/namespaces").json()[0]["revision"]
    app.delete(f"/api/namespaces/{namespace['id']}/sources/{source['id']}?expected_revision={rev}&operation_id=del-apollo-1234")

    # Verify query run trace has been redacted
    run = app.get(f"/api/namespaces/{namespace['id']}/query-runs/{trace_id}").json()
    assert run["status"] == "redacted"
    assert "Apollo confidential test data" not in json.dumps(run["answer"])
    assert "[source deleted]" in json.dumps(run["answer"])


def test_idempotency_rejects_different_payload(tmp_path: Path):
    app = TestClient(create_app(Settings(app_data_dir=tmp_path, sarvam_api_key="")))
    namespace = app.post("/api/namespaces", json={"name": "PayloadIdempotency"}).json()
    record = '{"schema_version":1,"id":"pref","raw_asr":"i prefer short emails","formatted_text":"I prefer short emails."}'
    app.post(f"/api/namespaces/{namespace['id']}/imports", json={"jsonl": record})
    app.post("/api/worker/run-once")
    memory = app.get(f"/api/namespaces/{namespace['id']}/memories").json()[0]
    revision = app.get("/api/namespaces").json()[0]["revision"]

    op_id = "reused-op-12345678"
    first = app.post(
        f"/api/namespaces/{namespace['id']}/memories/{memory['id']}/corrections",
        json={"value": "concise emails", "expected_revision": revision, "operation_id": op_id},
    )
    assert first.status_code == 200

    # Same operation_id but different value -> 409 operation_payload_mismatch
    mismatch = app.post(
        f"/api/namespaces/{namespace['id']}/memories/{memory['id']}/corrections",
        json={"value": "long emails", "expected_revision": revision, "operation_id": op_id},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["code"] == "operation_payload_mismatch"

