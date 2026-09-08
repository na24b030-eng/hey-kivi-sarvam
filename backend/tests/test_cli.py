import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from kivi_memory.api import create_app
from kivi_memory.cli import command_reset
from kivi_memory.db import MemoryOperation, Namespace, QueryRun, Source, build_session_factory
from kivi_memory.settings import Settings


def test_reset_clears_namespace_data_but_keeps_the_workspace(tmp_path: Path, capsys):
    settings = Settings(app_data_dir=tmp_path, sarvam_api_key="")
    app = TestClient(create_app(settings))
    namespace = app.post("/api/namespaces", json={"name": "Reset me"}).json()
    record = '{"schema_version":1,"id":"pref","raw_asr":"i prefer short emails","formatted_text":"I prefer short emails."}'
    app.post(f"/api/namespaces/{namespace['id']}/imports", json={"jsonl": record})
    app.post("/api/worker/run-once")
    memory = app.get(f"/api/namespaces/{namespace['id']}/memories").json()[0]
    revision = app.get("/api/namespaces").json()[0]["revision"]
    app.post(
        f"/api/namespaces/{namespace['id']}/memories/{memory['id']}/suppressions",
        json={"expected_revision": revision, "operation_id": "reset-operation-123"},
    )
    app.post(
        f"/api/namespaces/{namespace['id']}/ask",
        json={"question": "What email style do I prefer?"},
    )

    assert command_reset(settings, "Reset me") == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "namespace": "Reset me",
        "deleted_sources": 1,
        "deleted_query_runs": 1,
        "deleted_operations": 1,
    }

    factory, _ = build_session_factory(settings)
    with factory() as session:
        assert session.scalar(select(func.count(Source.id))) == 0
        assert session.scalar(select(func.count(QueryRun.id))) == 0
        assert session.scalar(select(func.count(MemoryOperation.id))) == 0
        assert session.get(Namespace, namespace["id"]) is not None


def test_reset_missing_namespace_fails_without_creating_one(tmp_path: Path, capsys):
    settings = Settings(app_data_dir=tmp_path, sarvam_api_key="")
    assert command_reset(settings, "Missing") == 1
    assert json.loads(capsys.readouterr().out)["error"] == "namespace_not_found"
    factory, _ = build_session_factory(settings)
    with factory() as session:
        assert session.scalar(select(func.count(Namespace.id))) == 0
