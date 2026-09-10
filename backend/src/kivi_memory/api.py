from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select, text, update
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
from .embeddings import model_is_cached
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
    literal_origins = [o for o in settings.origins if not ("*" in o and o != "*")]
    wildcard_origins = [o for o in settings.origins if "*" in o and o != "*"]
    origin_regex = None
    if wildcard_origins:
        import re
        patterns = [re.escape(w).replace(r"\*", r"[a-zA-Z0-9_\-\.]+") for w in wildcard_origins]
        origin_regex = f"^({'|'.join(patterns)})$"

    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(literal_origins),
        allow_origin_regex=origin_regex,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID", "X-API-Key"],
    )

    if settings.api_key:
        @app.middleware("http")
        async def api_key_auth(request: Request, call_next):
            # Exempt health and non-api routes
            if request.url.path.startswith("/api") and request.url.path != "/api/health":
                client_key = request.headers.get("x-api-key")
                if not client_key or client_key != settings.api_key:
                    req_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex}"
                    return JSONResponse(
                        status_code=401,
                        content={
                            "code": "authentication_required",
                            "message": "Valid X-API-Key header required.",
                            "request_id": req_id,
                            "retryable": False,
                        },
                    )
            return await call_next(request)

    def session_dep():
        yield from get_session(factory)

    def request_id(request: Request) -> str:
        return request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex}"

    def fail(status: int, code: str, message: str, request: Request, *, retryable: bool = False, details: dict[str, Any] | None = None):
        raise HTTPException(status_code=status, detail={"code": code, "message": message, "request_id": request_id(request), "retryable": retryable, "details": details})

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return JSONResponse(content=exc.detail, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors = [
            {key: value for key, value in error.items() if key != "ctx"}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": "The request did not match the expected format.",
                "request_id": request_id(request),
                "retryable": False,
                "details": {"errors": errors},
            },
        )

    def namespace_or_404(namespace_id: str, db: Session, request: Request) -> Namespace:
        item = db.get(Namespace, namespace_id)
        if not item:
            fail(404, "missing_namespace", "Namespace was not found.", request)
        return item

    def replayed_operation(
        db: Session,
        operation_id: str,
        namespace_id: str,
        kind: str,
        target_id: str,
        request: Request,
        payload_hash: str | None = None,
    ) -> bool:
        operation = db.scalar(
            select(MemoryOperation).where(MemoryOperation.operation_id == operation_id)
        )
        if operation is None:
            return False
        if (
            operation.namespace_id != namespace_id
            or operation.kind != kind
            or operation.target_id != target_id
        ):
            fail(
                409,
                "operation_id_reused",
                "This operation ID was already used for a different change.",
                request,
            )
        if payload_hash and operation.payload_hash and operation.payload_hash != payload_hash:
            fail(
                409,
                "operation_payload_mismatch",
                "This operation ID was already used with a different request payload.",
                request,
            )
        return True

    def record_operation(
        db: Session,
        namespace_id: str,
        operation_id: str,
        kind: str,
        target_id: str,
        payload_hash: str | None = None,
    ) -> None:
        db.add(
            MemoryOperation(
                id=f"op_{uuid.uuid4().hex}",
                namespace_id=namespace_id,
                operation_id=operation_id,
                kind=kind,
                target_id=target_id,
                payload_hash=payload_hash,
            )
        )

    @app.get("/api/health")
    def health():
        return {"status": "alive", "version": "0.1.0"}

    @app.get("/api/readiness")
    def readiness(request: Request, db: Session = Depends(session_dep)):
        try:
            tables = set(db.scalars(text("SELECT name FROM sqlite_master WHERE type='table'")).all())
            required = {"namespaces", "sources", "source_chunks", "memories", "jobs", "embeddings", "memory_operations", "query_runs"}
            missing = required - tables
            if missing:
                fail(503, "schema_incomplete", f"Missing database tables: {sorted(missing)}", request, retryable=True)
            queued = db.scalar(select(func.count(Job.id)).where(Job.state == "queued")) or 0
            return {
                "status": "ready",
                "database": "ready",
                "provider_configured": bool(settings.sarvam_api_key),
                "embedding_model_cached": model_is_cached(settings),
                "queued_jobs": queued,
            }
        except SQLAlchemyError:
            fail(503, "database_unavailable", "The database is unavailable.", request, retryable=True)

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
        failed = 0
        while True:
            res = process_one_job(db, settings)
            if not res:
                break
            if res.get("state") == "failed":
                failed += 1
            else:
                processed += 1
        return {"state": "idle", "processed": processed, "failed": failed}

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
        clean_value = payload.value.strip()
        payload_hash = hashlib.sha256(clean_value.encode()).hexdigest()

        if replayed_operation(
            db, payload.operation_id, namespace_id, "correct", memory_id, request, payload_hash=payload_hash
        ):
            return {"id": memory_id, "revision": namespace.revision, "state": "already_applied"}

        memory = db.get(Memory, memory_id)
        if not memory or memory.namespace_id != namespace_id:
            fail(404, "missing_memory", "Memory was not found.", request)
        if memory.state != "active":
            fail(409, "memory_not_active", "Only active memories can be corrected.", request)

        # Atomic revision update
        update_res = db.execute(
            update(Namespace)
            .where(Namespace.id == namespace_id, Namespace.revision == payload.expected_revision)
            .values(revision=Namespace.revision + 1)
        )
        if update_res.rowcount == 0:
            cur = db.get(Namespace, namespace_id)
            fail(409, "stale_namespace", "Memory changed while you were editing it.", request, details={"revision": cur.revision if cur else None})

        memory.state = "superseded"
        replacement = Memory(
            id=f"mem_{uuid.uuid4().hex}",
            namespace_id=namespace_id,
            kind=memory.kind,
            subject=memory.subject,
            predicate=memory.predicate,
            value=clean_value,
            scope=memory.scope,
            source_id=memory.source_id,
            supersedes_id=memory.id,
        )
        db.add(replacement)
        record_operation(db, namespace_id, payload.operation_id, "correct", memory_id, payload_hash=payload_hash)
        db.commit()
        db.refresh(namespace)
        return {"id": replacement.id, "revision": namespace.revision, "state": "active"}

    @app.post("/api/namespaces/{namespace_id}/memories/{memory_id}/suppressions")
    def suppress_memory(namespace_id: str, memory_id: str, payload: SuppressionRequest, request: Request, db: Session = Depends(session_dep)):
        namespace = namespace_or_404(namespace_id, db, request)
        if replayed_operation(
            db, payload.operation_id, namespace_id, "suppress", memory_id, request
        ):
            return {"id": memory_id, "revision": namespace.revision, "state": "already_applied"}

        memory = db.get(Memory, memory_id)
        if not memory or memory.namespace_id != namespace_id:
            fail(404, "missing_memory", "Memory was not found.", request)

        # Atomic revision update
        update_res = db.execute(
            update(Namespace)
            .where(Namespace.id == namespace_id, Namespace.revision == payload.expected_revision)
            .values(revision=Namespace.revision + 1)
        )
        if update_res.rowcount == 0:
            cur = db.get(Namespace, namespace_id)
            fail(409, "stale_namespace", "Memory changed while you were editing it.", request, details={"revision": cur.revision if cur else None})

        memory.state = "suppressed"
        record_operation(db, namespace_id, payload.operation_id, "suppress", memory_id)
        db.commit()
        db.refresh(namespace)
        return {"id": memory.id, "revision": namespace.revision, "state": memory.state}

    @app.delete("/api/namespaces/{namespace_id}/sources/{source_id}")
    def delete_source(namespace_id: str, source_id: str, expected_revision: int, operation_id: str, request: Request, db: Session = Depends(session_dep)):
        namespace = namespace_or_404(namespace_id, db, request)
        if not 8 <= len(operation_id.strip()) <= 100:
            fail(422, "invalid_operation_id", "Operation ID must contain 8 to 100 characters.", request)
        if replayed_operation(
            db, operation_id, namespace_id, "delete_source", source_id, request
        ):
            return {"deleted": source_id, "revision": namespace.revision, "state": "already_applied"}

        source = db.get(Source, source_id)
        if not source or source.namespace_id != namespace_id:
            fail(404, "missing_source", "Source was not found.", request)

        # Atomic revision update
        update_res = db.execute(
            update(Namespace)
            .where(Namespace.id == namespace_id, Namespace.revision == expected_revision)
            .values(revision=Namespace.revision + 1)
        )
        if update_res.rowcount == 0:
            cur = db.get(Namespace, namespace_id)
            fail(409, "stale_namespace", "History changed while you were deleting it.", request, details={"revision": cur.revision if cur else None})

        # Redact deleted source content comprehensively from all stored query runs (Item 12)
        query_runs = db.scalars(select(QueryRun).where(QueryRun.namespace_id == namespace_id)).all()
        for qrun in query_runs:
            try:
                qdata = json.loads(qrun.answer_json)
                evidence_list = qdata.get("evidence", [])
                matched = False
                for ev in evidence_list:
                    if ev.get("id") == source_id:
                        ev["text"] = "[source deleted]"
                        matched = True
                if matched:
                    qdata["answer"] = "[This answer referenced a deleted source and has been redacted.]"
                    qdata["status"] = "redacted"
                    for claim in qdata.get("claims", []):
                        if source_id in claim.get("evidence_ids", []):
                            claim["text"] = "[redacted]"
                    qrun.status = "redacted"
                    qrun.answer_json = json.dumps(qdata, ensure_ascii=False)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue

        record_operation(db, namespace_id, operation_id, "delete_source", source_id)
        db.delete(source)
        db.commit()
        db.refresh(namespace)
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
    frontend = next((path.resolve() for path in frontend_candidates if path and path.exists()), None)
    if frontend is not None:
        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str, request: Request):
            if path == "api" or path.startswith("api/"):
                fail(404, "missing_api_route", "API route was not found.", request)
            candidate = (frontend / path).resolve()
            if path and candidate.is_relative_to(frontend) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(frontend / "index.html")
    return app
