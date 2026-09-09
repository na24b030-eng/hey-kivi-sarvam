import json
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from kivi_memory.api import create_app
from kivi_memory.db import Job, Source, build_session_factory, now
from kivi_memory.services import process_one_job
from kivi_memory.settings import Settings


def test_expired_job_is_reclaimed(tmp_path: Path):
    settings = Settings(app_data_dir=tmp_path, sarvam_api_key="")
    app = TestClient(create_app(settings))
    ns = app.post("/api/namespaces", json={"name": "Worker"}).json()
    rec = json.dumps({
        "schema_version": 1,
        "id": "job-test",
        "raw_asr": "lantern launch monday",
        "formatted_text": "Lantern launches Monday.",
    })
    app.post(f"/api/namespaces/{ns['id']}/imports", json={"jsonl": rec})

    factory, _ = build_session_factory(settings)
    with factory() as session:
        job = session.scalar(select(Job).where(Job.namespace_id == ns["id"]))
        assert job is not None
        # Simulate worker crashed while holding job, and lease expired 10 minutes ago
        job.state = "running"
        job.lease_until = now() - timedelta(minutes=10)
        job.worker_id = "dead_worker"
        session.commit()

    # Worker should reclaim and finish the expired job
    with factory() as session:
        result = process_one_job(session, settings)
        assert result is not None
        assert result["state"] == "completed"

    with factory() as session:
        source = session.scalar(select(Source).where(Source.external_id == "job-test"))
        assert source is not None
        assert source.processing_status == "ready"


def test_source_ineligible_cancels_job(tmp_path: Path):
    settings = Settings(app_data_dir=tmp_path, sarvam_api_key="")
    app = TestClient(create_app(settings))
    ns = app.post("/api/namespaces", json={"name": "Ineligible"}).json()
    rec = json.dumps({
        "schema_version": 1,
        "id": "inelig-test",
        "raw_asr": "lantern launch monday",
        "formatted_text": "Lantern launches Monday.",
    })
    app.post(f"/api/namespaces/{ns['id']}/imports", json={"jsonl": rec})

    factory, _ = build_session_factory(settings)
    with factory() as session:
        source = session.scalar(select(Source).where(Source.external_id == "inelig-test"))
        assert source is not None
        # Make source ineligible before worker finishes
        source.eligible = False
        session.commit()

    with factory() as session:
        result = process_one_job(session, settings)
        assert result is not None
        assert result["state"] == "cancelled"
