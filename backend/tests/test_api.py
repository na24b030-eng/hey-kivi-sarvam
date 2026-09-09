import json
import struct
from pathlib import Path

from fastapi.testclient import TestClient

from kivi_memory.api import create_app
from kivi_memory.db import Embedding, build_session_factory
from kivi_memory.settings import Settings


def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(Settings(app_data_dir=tmp_path, sarvam_api_key="")))


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
    assert result.json()["evidence"][0]["text"] == "Lantern launches Monday, not Friday."


def test_queued_source_is_not_searchable_until_processing_finishes(tmp_path: Path):
    app = client(tmp_path)
    namespace = app.post("/api/namespaces", json={"name": "Queued"}).json()
    record = '{"schema_version":1,"id":"queued","raw_asr":"lantern monday","formatted_text":"Lantern launches Monday."}'
    app.post(f"/api/namespaces/{namespace['id']}/imports", json={"jsonl": record})
    result = app.post(
        f"/api/namespaces/{namespace['id']}/ask",
        json={"question": "When does Lantern launch?"},
    )
    assert result.json()["status"] == "insufficient_evidence"


def test_answer_evidence_is_bounded_but_full_source_remains_inspectable(tmp_path: Path):
    settings = Settings(
        app_data_dir=tmp_path,
        sarvam_api_key="",
        max_evidence_chars_per_source=500,
        max_evidence_chars_total=1000,
    )
    app = TestClient(create_app(settings))
    namespace = app.post("/api/namespaces", json={"name": "Bounded"}).json()
    full_text = "Lantern launches Monday. " + ("detail " * 120)
    record = json.dumps(
        {
            "schema_version": 1,
            "id": "long-source",
            "raw_asr": full_text,
            "formatted_text": full_text,
        }
    )
    app.post(f"/api/namespaces/{namespace['id']}/imports", json={"jsonl": record})
    app.post("/api/worker/run-once")
    answer = app.post(
        f"/api/namespaces/{namespace['id']}/ask",
        json={"question": "When does Lantern launch?"},
    ).json()
    assert len(answer["evidence"][0]["text"]) <= 500
    assert answer["evidence"][0]["text"].endswith("…")
    source_id = answer["evidence"][0]["id"]
    detail = app.get(
        f"/api/namespaces/{namespace['id']}/sources/{source_id}"
    ).json()
    assert detail["formatted_text"] == full_text


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


def test_mixed_valid_and_invalid_import_is_atomic(tmp_path: Path):
    app = client(tmp_path)
    namespace = app.post("/api/namespaces", json={"name": "Atomic"}).json()
    valid = '{"schema_version":1,"id":"one","raw_asr":"one","formatted_text":"One."}'
    result = app.post(
        f"/api/namespaces/{namespace['id']}/imports",
        json={"jsonl": f"{valid}\nnot-json"},
    ).json()
    assert result["accepted"] == 0
    assert result["invalid"][0]["line"] == 2
    assert app.get(f"/api/namespaces/{namespace['id']}/sources").json() == []


def test_metadata_is_preserved_and_naive_timestamp_is_rejected(tmp_path: Path):
    app = client(tmp_path)
    namespace = app.post("/api/namespaces", json={"name": "Metadata"}).json()
    naive = '{"schema_version":1,"id":"naive","raw_asr":"text","formatted_text":"Text.","occurred_at":"2026-09-08T10:30:00"}'
    preview = app.post(
        f"/api/namespaces/{namespace['id']}/imports/validate",
        json={"jsonl": naive},
    ).json()
    assert preview["invalid_count"] == 1

    record = '{"schema_version":1,"id":"metadata","raw_asr":"text","formatted_text":"Text.","occurred_at":"2026-09-08T10:30:00+05:30","language_hints":["en","hi"],"capture_device":"headset"}'
    app.post(f"/api/namespaces/{namespace['id']}/imports", json={"jsonl": record})
    source = app.get(f"/api/namespaces/{namespace['id']}/sources").json()[0]
    assert source["context"]["language_hints"] == ["en", "hi"]
    assert source["context"]["extra"]["capture_device"] == "headset"


def test_blank_identifiers_questions_and_transcripts_are_rejected(tmp_path: Path):
    app = client(tmp_path)
    invalid_namespace = app.post("/api/namespaces", json={"name": "   "})
    assert invalid_namespace.status_code == 422
    assert invalid_namespace.json()["code"] == "validation_error"
    assert invalid_namespace.json()["request_id"].startswith("req_")
    namespace = app.post("/api/namespaces", json={"name": "Validation"}).json()
    blank_record = '{"schema_version":1,"id":"blank","raw_asr":" ","formatted_text":""}'
    preview = app.post(
        f"/api/namespaces/{namespace['id']}/imports/validate",
        json={"jsonl": blank_record},
    )
    assert preview.json()["invalid_count"] == 1
    question = app.post(
        f"/api/namespaces/{namespace['id']}/ask",
        json={"question": "   "},
    )
    assert question.status_code == 422


def test_distinct_workspace_names_survive_slug_collisions(tmp_path: Path):
    app = client(tmp_path)
    first = app.post("/api/namespaces", json={"name": "Team Notes"}).json()
    second = app.post("/api/namespaces", json={"name": "Team-Notes"}).json()
    assert first["id"] != second["id"]
    assert {item["name"] for item in app.get("/api/namespaces").json()} == {
        "Team Notes",
        "Team-Notes",
    }


def test_replacing_a_source_excludes_the_old_version(tmp_path: Path):
    app = client(tmp_path)
    namespace = app.post("/api/namespaces", json={"name": "Versions"}).json()
    first = '{"schema_version":1,"id":"launch","raw_asr":"friday launch","formatted_text":"Lantern launches Friday."}'
    second = '{"schema_version":1,"id":"launch","raw_asr":"monday launch","formatted_text":"Lantern launches Monday."}'
    app.post(f"/api/namespaces/{namespace['id']}/imports", json={"jsonl": first})
    app.post("/api/worker/run-once")
    replaced = app.post(
        f"/api/namespaces/{namespace['id']}/imports",
        json={"jsonl": second, "replace_conflicts": True},
    )
    assert replaced.json()["accepted"] == 1
    app.post("/api/worker/run-once")
    sources = app.get(f"/api/namespaces/{namespace['id']}/sources").json()
    assert len(sources) == 2
    assert sum(source["eligible"] for source in sources) == 1
    answer = app.post(
        f"/api/namespaces/{namespace['id']}/ask",
        json={"question": "When does Lantern launch?"},
    ).json()
    assert [item["text"] for item in answer["evidence"]] == ["Lantern launches Monday."]


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
    factory, _ = build_session_factory(Settings(app_data_dir=tmp_path, sarvam_api_key=""))
    with factory() as session:
        assert session.query(Embedding).count() == 2


def test_spa_static_files_cannot_escape_the_frontend_directory(tmp_path: Path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("safe-index", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("must-not-be-served", encoding="utf-8")
    app = TestClient(
        create_app(
            Settings(
                app_data_dir=tmp_path / "data",
                frontend_dist_dir=frontend,
                sarvam_api_key="",
            )
        )
    )
    response = app.get("/%2e%2e/secret.txt")
    assert response.status_code == 200
    assert response.text == "safe-index"

    missing_api = app.get("/api/does-not-exist")
    assert missing_api.status_code == 404
    assert missing_api.headers["content-type"].startswith("application/json")
    assert missing_api.json()["code"] == "missing_api_route"


def test_multiple_facts_extracted_from_single_source(tmp_path: Path):
    app = client(tmp_path)
    ns = app.post("/api/namespaces", json={"name": "MultiFacts"}).json()
    record = json.dumps({
        "schema_version": 1,
        "id": "multi-source",
        "raw_asr": "lantern launch monday we prefer email updates",
        "formatted_text": "Lantern launches Monday. We prefer email updates.",
    })
    app.post(f"/api/namespaces/{ns['id']}/imports", json={"jsonl": record})
    app.post("/api/worker/drain")

    memories = app.get(f"/api/namespaces/{ns['id']}/memories").json()
    assert len(memories) == 2
    kinds = {m["kind"] for m in memories}
    assert "fact" in kinds
    assert "preference" in kinds


def test_drain_reports_failure_count(tmp_path: Path):
    app = client(tmp_path)
    ns = app.post("/api/namespaces", json={"name": "DrainFailures"}).json()
    record = json.dumps({
        "schema_version": 1,
        "id": "drain-test",
        "raw_asr": "test drain",
        "formatted_text": "Test drain.",
    })
    app.post(f"/api/namespaces/{ns['id']}/imports", json={"jsonl": record})
    res = app.post("/api/worker/drain").json()
    assert res["state"] == "idle"
    assert res["processed"] == 1
    assert res["failed"] == 0


def test_readiness_checks_tables(tmp_path: Path):
    app = client(tmp_path)
    res = app.get("/api/readiness")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ready"
    assert data["database"] == "ready"
    assert "queued_jobs" in data


def test_api_key_required_when_configured(tmp_path: Path):
    settings = Settings(app_data_dir=tmp_path, api_key="secret-token-12345", sarvam_api_key="")
    app = TestClient(create_app(settings))

    # Health is always public
    health = app.get("/api/health")
    assert health.status_code == 200

    # Read/write endpoints require X-API-Key
    unauth = app.get("/api/namespaces")
    assert unauth.status_code == 401
    assert unauth.json()["code"] == "authentication_required"

    # With proper header, access is granted
    auth = app.get("/api/namespaces", headers={"X-API-Key": "secret-token-12345"})
    assert auth.status_code == 200


def test_concurrent_revision_check_rejects_stale(tmp_path: Path):
    app = client(tmp_path)
    ns = app.post("/api/namespaces", json={"name": "CASRevision"}).json()
    rec = json.dumps({
        "schema_version": 1,
        "id": "pref",
        "raw_asr": "i prefer short emails",
        "formatted_text": "I prefer short emails.",
    })
    app.post(f"/api/namespaces/{ns['id']}/imports", json={"jsonl": rec})
    app.post("/api/worker/drain")
    memory = app.get(f"/api/namespaces/{ns['id']}/memories").json()[0]
    initial_rev = app.get("/api/namespaces").json()[0]["revision"]

    # First modification passes with initial_rev
    first = app.post(
        f"/api/namespaces/{ns['id']}/memories/{memory['id']}/corrections",
        json={"value": "concise", "expected_revision": initial_rev, "operation_id": "op-cas-11111"},
    )
    assert first.status_code == 200

    # Second concurrent modification that still sends initial_rev must fail with 409 stale_namespace
    second = app.post(
        f"/api/namespaces/{ns['id']}/memories/{first.json()['id']}/corrections",
        json={"value": "ultra-concise", "expected_revision": initial_rev, "operation_id": "op-cas-22222"},
    )
    assert second.status_code == 409
    assert second.json()["code"] == "stale_namespace"

