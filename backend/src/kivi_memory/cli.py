from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import uvicorn
from sqlalchemy import delete, func, select

from .api import create_app
from .db import (
    Embedding,
    Entity,
    EntityRelation,
    Job,
    Memory,
    MemoryOperation,
    Namespace,
    QueryRun,
    Source,
    SourceChunk,
    build_session_factory,
)
from .embeddings import load_encoder
from .evaluation import run_suite
from .services import ensure_namespace, import_records, process_one_job
from .settings import Settings


def command_doctor(settings: Settings, download_embedding: bool = False) -> int:
    factory, _ = build_session_factory(settings)
    with factory() as session:
        session.execute(__import__("sqlalchemy").text("SELECT 1"))
    embedding = "not_downloaded"
    if download_embedding:
        model = load_encoder(settings, download=True)
        dimensions = len(model.encode(["query: Kivi embedding check"], normalize_embeddings=True)[0])
        embedding = {"model": settings.embedding_model, "dimensions": dimensions, "cache": str(settings.embeddings_dir)}
    print(json.dumps({"status": "ready", "database": settings.db_url, "provider_configured": bool(settings.sarvam_api_key), "embedding": embedding}))
    return 0


def command_migrate() -> int:
    root = Path(__file__).resolve().parents[2]
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(root / "alembic.ini"), "upgrade", "head"],
        check=False,
        cwd=root,
    ).returncode


def command_import(settings: Settings, namespace_name: str, file_path: Path) -> int:
    factory, _ = build_session_factory(settings)
    raw = file_path.read_text(encoding="utf-8")
    with factory() as session:
        namespace = ensure_namespace(session, namespace_name)
        result = import_records(session, namespace, raw, settings.max_record_bytes, False)
    print(json.dumps(result, indent=2))
    if result.get("invalid") or result.get("accepted", 0) == 0:
        return 1
    return 0


def command_worker(settings: Settings, once: bool, drain: bool) -> int:
    factory, _ = build_session_factory(settings)
    failed_any = False
    while True:
        with factory() as session:
            result = process_one_job(session, settings)
        if result:
            print(json.dumps(result))
            if result.get("state") == "failed":
                failed_any = True
        if once:
            return 1 if (result and result.get("state") == "failed") else 0
        if drain and not result:
            return 1 if failed_any else 0
        if drain:
            continue
        time.sleep(1 if result else 2)


def command_seed(settings: Settings, namespace_name: str) -> int:
    root = Path(__file__).resolve().parents[3]
    file_path = root / "data" / "synthetic-500.jsonl"
    if not file_path.exists():
        print("Synthetic corpus is missing. Run data/generate_synthetic.py first.", file=sys.stderr)
        return 1
    return command_import(settings, namespace_name, file_path)


def command_inspect(settings: Settings, namespace_name: str) -> int:
    factory, _ = build_session_factory(settings)
    with factory() as session:
        namespace = ensure_namespace(session, namespace_name)
        jobs = session.scalars(select(Job).where(Job.namespace_id == namespace.id)).all()
        sources = session.scalars(select(Source).where(Source.namespace_id == namespace.id)).all()
        memories = session.scalars(select(Memory).where(Memory.namespace_id == namespace.id)).all()
        embeddings = session.scalar(
            select(func.count(Embedding.id))
            .join_from(Embedding, SourceChunk)
            .join(Source, SourceChunk.source_id == Source.id)
            .where(Source.namespace_id == namespace.id)
        ) or 0
        print(json.dumps({
            "namespace": namespace.id,
            "revision": namespace.revision,
            "sources": len(sources),
            "ready_sources": sum(source.processing_status == "ready" for source in sources),
            "memories": {state: sum(memory.state == state for memory in memories) for state in {memory.state for memory in memories}},
            "embeddings": embeddings,
            "jobs": {state: sum(job.state == state for job in jobs) for state in {job.state for job in jobs}},
        }, indent=2))
    return 0


def command_reset(settings: Settings, namespace_name: str) -> int:
    factory, _ = build_session_factory(settings)
    with factory() as session:
        namespace = session.scalar(
            select(Namespace).where(Namespace.name == namespace_name.strip())
        )
        if namespace is None:
            print(json.dumps({"error": "namespace_not_found", "namespace": namespace_name}))
            return 1
        source_ids = [source.id for source in session.scalars(select(Source).where(Source.namespace_id == namespace.id)).all()]
        query_runs = session.scalar(
            select(func.count(QueryRun.id)).where(QueryRun.namespace_id == namespace.id)
        ) or 0
        operations = session.scalar(
            select(func.count(MemoryOperation.id)).where(MemoryOperation.namespace_id == namespace.id)
        ) or 0
        session.execute(delete(QueryRun).where(QueryRun.namespace_id == namespace.id))
        session.execute(delete(MemoryOperation).where(MemoryOperation.namespace_id == namespace.id))
        session.execute(delete(EntityRelation).where(EntityRelation.namespace_id == namespace.id))
        session.execute(delete(Entity).where(Entity.namespace_id == namespace.id))
        for source_id in source_ids:
            source = session.get(Source, source_id)
            if source:
                session.delete(source)
        namespace.revision += 1
        session.commit()
    print(json.dumps({
        "namespace": namespace_name,
        "deleted_sources": len(source_ids),
        "deleted_query_runs": query_runs,
        "deleted_operations": operations,
    }))
    return 0


def command_evaluate(settings: Settings, namespace_name: str, suite: Path, output: Path, offline: bool = False) -> int:
    if offline:
        settings = settings.model_copy(update={"sarvam_api_key": None})
    factory, _ = build_session_factory(settings)
    with factory() as session:
        namespace = ensure_namespace(session, namespace_name)
        report = run_suite(session, namespace, suite, output, settings)
    print(json.dumps({key: report[key] for key in ("case_count", "passed", "evidence_accuracy")}, indent=2))
    if report.get("passed", 0) < report.get("case_count", 0):
        return 1
    return 0


def command_backfill_embeddings(settings: Settings, namespace_name: str | None = None) -> int:
    from .embeddings import encode_passage, model_is_cached
    if not model_is_cached(settings):
        print(json.dumps({"error": "embedding_model_not_cached", "model": settings.embedding_model}))
        return 1
    factory, _ = build_session_factory(settings)
    backfilled = 0
    with factory() as session:
        q = (
            select(SourceChunk)
            .outerjoin(Embedding, SourceChunk.id == Embedding.source_chunk_id)
            .where(Embedding.id.is_(None))
        )
        if namespace_name:
            q = q.join(Source, SourceChunk.source_id == Source.id).where(Source.namespace_id == namespace_name)
        chunks = session.scalars(q).all()
        for chunk in chunks:
            encoded = encode_passage(settings, chunk.text)
            if encoded:
                vector, dimensions = encoded
                session.add(
                    Embedding(
                        id=f"emb_{uuid.uuid4().hex}",
                        source_chunk_id=chunk.id,
                        model=settings.embedding_model,
                        dimensions=dimensions,
                        vector=vector,
                        content_hash=hashlib.sha256(chunk.text.encode()).hexdigest(),
                    )
                )
                backfilled += 1
        session.commit()
    print(json.dumps({"backfilled_embeddings": backfilled}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="kivi-memory")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor"); doctor.add_argument("--download-embedding", action="store_true")
    sub.add_parser("migrate")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    worker = sub.add_parser("worker"); worker.add_argument("--once", action="store_true"); worker.add_argument("--drain", action="store_true")
    imp = sub.add_parser("import"); imp.add_argument("--namespace", required=True); imp.add_argument("--file", type=Path, required=True)
    seed = sub.add_parser("seed"); seed.add_argument("--namespace", default="demo")
    inspect = sub.add_parser("inspect"); inspect.add_argument("--namespace", required=True)
    reset = sub.add_parser("reset"); reset.add_argument("--namespace", required=True)
    evaluate = sub.add_parser("evaluate"); evaluate.add_argument("--namespace", required=True); evaluate.add_argument("--suite", type=Path, required=True); evaluate.add_argument("--output", type=Path, required=True); evaluate.add_argument("--offline", action="store_true")
    backfill = sub.add_parser("backfill-embeddings"); backfill.add_argument("--namespace", default=None)
    args = parser.parse_args()
    settings = Settings()
    if args.command == "doctor": return command_doctor(settings, args.download_embedding)
    if args.command == "migrate": return command_migrate()
    if args.command == "serve":
        factory, _ = build_session_factory(settings)
        with factory() as session:
            try:
                demo = ensure_namespace(session, "demo")
                session.commit()
                count = session.scalar(select(func.count(Source.id)).where(Source.namespace_id == demo.id)) or 0
                if count == 0:
                    command_seed(settings, "demo")
                    command_worker(settings, once=False, drain=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] Startup demo initialization failed: {exc}", file=sys.stderr)
        uvicorn.run(create_app(settings), host=args.host, port=args.port)
        return 0
    if args.command == "worker": return command_worker(settings, args.once, args.drain)
    if args.command == "import": return command_import(settings, args.namespace, args.file)
    if args.command == "seed": return command_seed(settings, args.namespace)
    if args.command == "inspect": return command_inspect(settings, args.namespace)
    if args.command == "reset": return command_reset(settings, args.namespace)
    if args.command == "evaluate": return command_evaluate(settings, args.namespace, args.suite, args.output, args.offline)
    if args.command == "backfill-embeddings": return command_backfill_embeddings(settings, args.namespace)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
