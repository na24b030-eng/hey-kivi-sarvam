from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .contracts import (
    AskRequest,
    CorrectionRequest,
    ImportRequest,
    NamespaceCreate,
    SuppressionRequest,
    ValidateImportRequest,
)
from .db import (
    Job,
    Memory,
    MemoryOperation,
    Namespace,
    QueryRun,
    Source,
    build_session_factory,
    get_session,
)
from .services import (
    ask,
    ensure_namespace,
    import_records,
    preview_import,
    process_one_job,
    source_payload,
)
from .settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    factory, engine = build_session_factory(settings)
    app = FastAPI(title="Kivi Memory Workbench", version="0.1.0")
    app.state.session_factory = factory
    app.state.engine = engine
    app.add_middleware(CORSMiddleware, allow_origins=sorted(settings.origins), allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"])

    def session_dep():
        yield from get_session(factory)

    def request_id(request: Request) -> str:
        return request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex}"

    def fail(status: int, code: str, message: str, request: Request, *, retryable: bool = False, details: dict[str, Any] | None = None):
        raise HTTPException(status_code=status, detail={"code": code, "message": message, "request_id": request_id(request), "retryable": retryable, "details": details})

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return Response(content=json.dumps(exc.detail), status_code=exc.status_code, media_type="application/json")

    def namespace_or_404(namespace_id: str, db: Session, request: Request) -> Namespace:
        item = db.get(Namespace, namespace_id)
        if not item:
            fail(404, "missing_namespace", "Namespace was not found.", request)
        return item

    def operation_exists(db: Session, operation_id: str) -> bool:
        return db.scalar(select(MemoryOperation).where(MemoryOperation.operation_id == operation_id)) is not None

    def record_operation(db: Session, namespace_id: str, operation_id: str, kind: str, target_id: str) -> None:
        db.add(MemoryOperation(id=f"op_{uuid.uuid4().hex}", namespace_id=namespace_id, operation_id=operation_id, kind=kind, target_id=target_id))

    @app.get("/api/health")
    def health():
        return {"status": "alive", "version": "0.1.0"}

    @app.get("/api/readiness")
    def readiness(db: Session = Depends(session_dep)):
        try:
            db.execute(__import__("sqlalchemy").text("SELECT 1"))
            return {"status": "ready", "database": "ready", "provider_configured": bool(settings.sarvam_api_key)}
        except SQLAlchemyError:
            return {"status": "not_ready", "database": "unavailable"}

    @app.post("/api/namespaces")
    def create_namespace(payload: NamespaceCreate, db: Session = Depends(session_dep)):
        item = ensure_namespace(db, payload.name)
        db.commit()
        return {"id": item.id, "name": item.name, "revision": item.revision}

    @app.get("/api/namespaces")
    def list_namespaces(db: Session = Depends(session_dep)):
        return [{"id": item.id, "name": item.name, "revision": item.revision} for item in db.scalars(select(Namespace).order_by(Namespace.created_at)).all()]

    @app.post("/api/namespaces/{namespace_id}/imports/validate")
    def validate_import(namespace_id: str, payload: ValidateImportRequest, request: Request, db: Session = Depends(session_dep)):
        namespace_or_404(namespace_id, db, request)
        if len(payload.jsonl.encode("utf-8")) > settings.max_import_bytes:
            fail(422, "import_too_large", "Import exceeds maximum size.", request)
        return preview_import(payload.jsonl, settings.max_record_bytes)

    @app.post("/api/namespaces/{namespace_id}/imports")
    def create_import(namespace_id: str, payload: ImportRequest, request: Request, db: Session = Depends(session_dep)):
        namespace = namespace_or_404(namespace_id, db, request)
        if len(payload.jsonl.encode("utf-8")) > settings.max_import_bytes:
            fail(422, "import_too_large", "Import exceeds maximum size.", request)
        result = import_records(db, namespace, payload.jsonl, settings.max_record_bytes, payload.replace_conflicts)
        return result

    @app.get("/api/namespaces/{namespace_id}/jobs")
    def list_jobs(namespace_id: str, request: Request, db: Session = Depends(session_dep)):
        namespace_or_404(namespace_id, db, request)
        return [{"id": job.id, "source_id": job.source_id, "state": job.state, "attempts": job.attempts, "progress": job.progress, "error": job.error} for job in db.scalars(select(Job).where(Job.namespace_id == namespace_id).order_by(Job.created_at.desc())).all()]

    @app.post("/api/namespaces/{namespace_id}/jobs/{job_id}/retry")
    def retry_job(namespace_id: str, job_id: str, request: Request, db: Session = Depends(session_dep)):
        namespace_or_404(namespace_id, db, request)
        job = db.get(Job, job_id)
        if not job or job.namespace_id != namespace_id:
            fail(404, "missing_job", "Job was not found.", request)
        if job.state not in {"failed", "cancelled"}:
            fail(409, "job_not_retryable", "Only failed or cancelled jobs can be retried.", request)
        job.state, job.error, job.progress, job.lease_until = "queued", None, 0, None
        db.commit()
        return {"id": job.id, "state": job.state, "attempts": job.attempts}

    @app.post("/api/worker/run-once")
    def worker_once(db: Session = Depends(session_dep)):
        return process_one_job(db, settings) or {"state": "idle"}

    @app.post("/api/worker/drain")
    def worker_drain(db: Session = Depends(session_dep)):
        """Process queued local jobs in one request after a desktop paste import."""
        processed = 0
        while process_one_job(db, settings):
            processed += 1
        return {"state": "idle", "processed": processed}

    @app.get("/api/namespaces/{namespace_id}/sources")
    def list_sources(namespace_id: str, request: Request, q: str = "", db: Session = Depends(session_dep)):
        namespace_or_404(namespace_id, db, request)
        items = db.scalars(select(Source).where(Source.namespace_id == namespace_id).order_by(Source.ingested_at.desc())).all()
        if q.strip():
            needle = q.casefold()
            items = [item for item in items if needle in item.raw_asr.casefold() or needle in item.formatted_text.casefold()]
        return [source_payload(item) for item in items]

    @app.get("/api/namespaces/{namespace_id}/sources/{source_id}")
    def get_source(namespace_id: str, source_id: str, request: Request, db: Session = Depends(session_dep)):
        namespace_or_404(namespace_id, db, request)
        source = db.get(Source, source_id)
        if not source or source.namespace_id != namespace_id:
            fail(404, "missing_source", "Source was not found.", request)
        payload = source_payload(source)
        payload["chunks"] = [{"id": chunk.id, "view": chunk.view, "text": chunk.text, "start": chunk.start_offset, "end": chunk.end_offset} for chunk in source.chunks]
        payload["memories"] = [{"id": memory.id, "kind": memory.kind, "subject": memory.subject, "predicate": memory.predicate, "value": memory.value, "state": memory.state} for memory in db.scalars(select(Memory).where(Memory.source_id == source.id)).all()]
        return payload

    @app.post("/api/namespaces/{namespace_id}/ask")
    def ask_kivi(namespace_id: str, payload: AskRequest, request: Request, db: Session = Depends(session_dep)):
        namespace = namespace_or_404(namespace_id, db, request)
        return ask(db, namespace, payload.question, payload.mode, settings)

    @app.get("/api/namespaces/{namespace_id}/memories")
    def list_memories(namespace_id: str, request: Request, db: Session = Depends(session_dep)):
        namespace_or_404(namespace_id, db, request)
        return [{"id": memory.id, "kind": memory.kind, "subject": memory.subject, "predicate": memory.predicate, "value": memory.value, "scope": memory.scope, "state": memory.state, "source_id": memory.source_id} for memory in db.scalars(select(Memory).where(Memory.namespace_id == namespace_id).order_by(Memory.created_at.desc())).all()]

    @app.post("/api/namespaces/{namespace_id}/memories/{memory_id}/corrections")
    def correct_memory(namespace_id: str, memory_id: str, payload: CorrectionRequest, request: Request, db: Session = Depends(session_dep)):
        namespace = namespace_or_404(namespace_id, db, request)
        if operation_exists(db, payload.operation_id):
            return {"id": memory_id, "revision": namespace.revision, "state": "already_applied"}
        if namespace.revision != payload.expected_revision:
            fail(409, "stale_namespace", "Memory changed while you were editing it.", request, details={"revision": namespace.revision})
        memory = db.get(Memory, memory_id)
        if not memory or memory.namespace_id != namespace_id:
            fail(404, "missing_memory", "Memory was not found.", request)
        memory.state = "superseded"
        replacement = Memory(id=f"mem_{uuid.uuid4().hex}", namespace_id=namespace_id, kind=memory.kind, subject=memory.subject, predicate=memory.predicate, value=payload.value, scope=memory.scope, source_id=memory.source_id, supersedes_id=memory.id)
        db.add(replacement)
        record_operation(db, namespace_id, payload.operation_id, "correct", memory_id)
        namespace.revision += 1
        db.commit()
        return {"id": replacement.id, "revision": namespace.revision, "state": "active"}

    @app.post("/api/namespaces/{namespace_id}/memories/{memory_id}/suppressions")
    def suppress_memory(namespace_id: str, memory_id: str, payload: SuppressionRequest, request: Request, db: Session = Depends(session_dep)):
        namespace = namespace_or_404(namespace_id, db, request)
        if operation_exists(db, payload.operation_id):
            return {"id": memory_id, "revision": namespace.revision, "state": "already_applied"}
        if namespace.revision != payload.expected_revision:
            fail(409, "stale_namespace", "Memory changed while you were editing it.", request, details={"revision": namespace.revision})
        memory = db.get(Memory, memory_id)
        if not memory or memory.namespace_id != namespace_id:
            fail(404, "missing_memory", "Memory was not found.", request)
        memory.state = "suppressed"
        record_operation(db, namespace_id, payload.operation_id, "suppress", memory_id)
        namespace.revision += 1
        db.commit()
        return {"id": memory.id, "revision": namespace.revision, "state": memory.state}

    @app.delete("/api/namespaces/{namespace_id}/sources/{source_id}")
    def delete_source(namespace_id: str, source_id: str, expected_revision: int, operation_id: str, request: Request, db: Session = Depends(session_dep)):
        namespace = namespace_or_404(namespace_id, db, request)
        if operation_exists(db, operation_id):
            return {"deleted": source_id, "revision": namespace.revision, "state": "already_applied"}
        if namespace.revision != expected_revision:
            fail(409, "stale_namespace", "History changed while you were deleting it.", request, details={"revision": namespace.revision})
        source = db.get(Source, source_id)
        if not source or source.namespace_id != namespace_id:
            fail(404, "missing_source", "Source was not found.", request)
        record_operation(db, namespace_id, operation_id, "delete_source", source_id)
        db.delete(source)
        namespace.revision += 1
        db.commit()
        return {"deleted": source_id, "revision": namespace.revision}

    @app.get("/api/namespaces/{namespace_id}/query-runs/{run_id}")
    def query_run(namespace_id: str, run_id: str, request: Request, db: Session = Depends(session_dep)):
        namespace_or_404(namespace_id, db, request)
        run = db.get(QueryRun, run_id)
        if not run or run.namespace_id != namespace_id:
            fail(404, "missing_query_run", "Query trace was not found.", request)
        return {"id": run.id, "question": run.question, "status": run.status, "revision": run.revision, "answer": json.loads(run.answer_json), "created_at": run.created_at}

    @app.get("/api/namespaces/{namespace_id}/operations")
    def list_operations(namespace_id: str, request: Request, db: Session = Depends(session_dep)):
        namespace_or_404(namespace_id, db, request)
        return [{"kind": item.kind, "target_id": item.target_id, "reason": item.reason, "created_at": item.created_at} for item in db.scalars(select(MemoryOperation).where(MemoryOperation.namespace_id == namespace_id).order_by(MemoryOperation.created_at.desc())).all()]

    frontend_candidates = [
        settings.frontend_dist_dir,
        Path.cwd().parent / "frontend" / "dist",
        Path(__file__).resolve().parents[3] / "frontend" / "dist",
    ]
    frontend = next((path for path in frontend_candidates if path and path.exists()), None)
    if frontend is not None:
        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            candidate = frontend / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(frontend / "index.html")
    return app
