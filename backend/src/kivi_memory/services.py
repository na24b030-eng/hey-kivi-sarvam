from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .contracts import TranscriptRecord
from .db import Embedding, Job, Memory, Namespace, QueryRun, Source, SourceChunk, now
from .embeddings import content_hash, encode_passage
from .generation import synthesize
from .retrieval import active_memories, search_sources, suppressed_source_ids
from .settings import Settings


def ident(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def checksum(record: TranscriptRecord) -> str:
    payload = record.model_dump(mode="json", exclude_none=True)
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def ensure_namespace(session: Session, name: str) -> Namespace:
    clean_name = name.strip()
    key = re.sub(r"[^a-z0-9_-]+", "-", clean_name.casefold()).strip("-")[:64] or "default"
    namespace = session.get(Namespace, key)
    if namespace is not None and normalize(namespace.name) != normalize(clean_name):
        suffix = hashlib.sha256(clean_name.casefold().encode()).hexdigest()[:8]
        key = f"{key[:55].rstrip('-')}-{suffix}"
        namespace = session.get(Namespace, key)
    if namespace is None:
        namespace = Namespace(id=key, name=clean_name)
        session.add(namespace)
        session.flush()
    return namespace


def parse_jsonl(raw: str, max_record_bytes: int) -> tuple[list[TranscriptRecord], list[dict[str, Any]]]:
    records: list[TranscriptRecord] = []
    errors: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > max_record_bytes:
            errors.append({"line": line_number, "message": "record exceeds size limit"})
            continue
        try:
            item = TranscriptRecord.model_validate_json(line)
            records.append(item)
        except Exception as exc:  # noqa: BLE001 - malformed untrusted JSON must become a row-level error.
            errors.append({"line": line_number, "message": str(exc)})
    return records, errors


def preview_import(raw: str, max_record_bytes: int) -> dict[str, Any]:
    records, errors = parse_jsonl(raw, max_record_bytes)
    return {
        "content_hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "valid_count": len(records),
        "invalid_count": len(errors),
        "errors": errors[:100],
        "records": [record.model_dump(mode="json") for record in records[:5]],
    }


def import_records(
    session: Session,
    namespace: Namespace,
    raw: str,
    max_record_bytes: int,
    replace_conflicts: bool,
) -> dict[str, Any]:
    records, errors = parse_jsonl(raw, max_record_bytes)
    if errors:
        return {
            "accepted": 0,
            "conflicts": 0,
            "invalid": errors,
            "job_ids": [],
            "revision": namespace.revision,
        }
    accepted = conflicts = 0
    job_ids: list[str] = []
    for record in records:
        digest = checksum(record)
        prior_sources = session.scalars(
            select(Source)
            .where(Source.namespace_id == namespace.id, Source.external_id == record.id)
            .order_by(Source.source_version.desc())
        ).all()
        existing = prior_sources[0] if prior_sources else None
        if existing and existing.checksum == digest:
            continue
        if existing and not replace_conflicts:
            conflicts += 1
            continue
        if existing:
            prior_ids = [source.id for source in prior_sources]
            for source in prior_sources:
                source.eligible = False
            for job in session.scalars(
                select(Job).where(Job.source_id.in_(prior_ids), Job.state == "queued")
            ).all():
                job.state, job.progress, job.lease_until = "cancelled", 100, None
            for memory in session.scalars(
                select(Memory).where(Memory.source_id.in_(prior_ids), Memory.state == "active")
            ).all():
                memory.state = "superseded"
        version = (existing.source_version + 1) if existing else 1
        metadata = dict(record.context)
        if record.language_hints:
            metadata["language_hints"] = record.language_hints
        if record.model_extra:
            metadata["extra"] = record.model_extra
        source = Source(
            id=ident("src"), namespace_id=namespace.id, external_id=record.id,
            source_version=version, raw_asr=record.raw_asr, formatted_text=record.formatted_text,
            occurred_at=record.occurred_at, timezone_name=record.timezone, app=record.app,
            context_json=json.dumps(metadata, ensure_ascii=False), checksum=digest,
            processing_status="queued",
        )
        session.add(source)
        session.flush()
        job = Job(id=ident("job"), namespace_id=namespace.id, source_id=source.id)
        session.add(job)
        job_ids.append(job.id)
        accepted += 1
    if accepted:
        namespace.revision += 1
    session.commit()
    return {"accepted": accepted, "conflicts": conflicts, "invalid": errors, "job_ids": job_ids, "revision": namespace.revision}


def chunk_text(value: str, length: int = 900) -> list[tuple[int, int, str]]:
    if not value:
        return []
    pieces: list[tuple[int, int, str]] = []
    start = 0
    while start < len(value):
        end = min(len(value), start + length)
        if end < len(value):
            boundary = max(value.rfind(". ", start, end), value.rfind("\n", start, end))
            if boundary > start + length // 3:
                end = boundary + 1
        segment = value[start:end]
        pieces.append((start, end, segment))
        start = end
    return pieces


def extract_memory_candidates(source: Source) -> list[tuple[str, str, str, str, str]]:
    """Conservative deterministic v1 extractor; source always remains available if it finds nothing."""
    value = source.formatted_text.strip()
    if not value:
        return []
    lower = value.casefold()
    if any(token in lower for token in ("if ", "maybe", "what if", "?")):
        return []
    candidates: list[tuple[str, str, str, str, str]] = []
    patterns = [
        (r"(?i)\b(.+?)\s+(?:launches|launch|is scheduled|scheduled)\s+(?:on|for)\s+(.+?)[.!]?$", "schedule"),
        (r"(?i)\b(?:i|we)\s+(?:prefer|like|want)\s+(.+?)[.!]?$", "preference"),
        (r"(?i)\b(.+?)\s+is\s+(.+?)[.!]?$", "is"),
    ]
    for pattern, predicate in patterns:
        match = re.search(pattern, value)
        if not match:
            continue
        if predicate == "preference":
            candidates.append(("preference", "user", predicate, match.group(1).strip(), "general"))
        else:
            candidates.append(("fact", match.group(1).strip(), predicate, match.group(2).strip(), "general"))
        break
    return candidates


def process_one_job(session: Session, settings: Settings | None = None) -> dict[str, Any] | None:
    job = session.scalar(select(Job).where(Job.state == "queued").order_by(Job.created_at).limit(1))
    if job is None:
        return None
    job.state, job.attempts, job.progress, job.lease_until = "running", job.attempts + 1, 5, now() + timedelta(minutes=2)
    session.commit()
    try:
        source = session.get(Source, job.source_id)
        if source is None or not source.eligible:
            job.state, job.progress = "cancelled", 100
            session.commit()
            return {"job_id": job.id, "state": job.state}
        group = hashlib.sha256(normalize(source.formatted_text or source.raw_asr).encode()).hexdigest()[:24]
        for view, value in (("raw", source.raw_asr), ("formatted", source.formatted_text)):
            for ordinal, (start, end, part) in enumerate(chunk_text(value)):
                chunk = SourceChunk(id=ident("chunk"), source_id=source.id, view=view, ordinal=ordinal,
                    start_offset=start, end_offset=end, text=part, search_text=normalize(part), duplicate_group_id=group)
                session.add(chunk)
                # Persist the parent before adding a vector that references its ID.
                # SQLAlchemy cannot infer ordering from two independently assigned
                # string foreign keys, and SQLite correctly rejects the reverse order.
                session.flush()
                if settings:
                    encoded = encode_passage(settings, part)
                    if encoded:
                        vector, dimensions = encoded
                        session.add(Embedding(id=ident("emb"), source_chunk_id=chunk.id, model=settings.embedding_model,
                            dimensions=dimensions, vector=vector, content_hash=content_hash(part)))
        job.progress = 50
        for kind, subject, predicate, value, scope in extract_memory_candidates(source):
            older = session.scalars(select(Memory).where(
                Memory.namespace_id == source.namespace_id, Memory.subject == subject,
                Memory.predicate == predicate, Memory.scope == scope, Memory.state == "active"
            )).all()
            for item in older:
                if item.value != value:
                    item.state = "superseded"
            session.add(Memory(id=ident("mem"), namespace_id=source.namespace_id, kind=kind, subject=subject,
                predicate=predicate, value=value, scope=scope, source_id=source.id))
        source.processing_status = "ready"
        namespace = session.get(Namespace, source.namespace_id)
        if namespace:
            namespace.revision += 1
        job.state, job.progress, job.lease_until = "completed", 100, None
        session.commit()
        return {"job_id": job.id, "state": job.state}
    except Exception as exc:
        session.rollback()
        job = session.get(Job, job.id)
        if job is None:
            raise
        job.state, job.error, job.lease_until = "failed", str(exc)[:500], None
        session.commit()
        return {"job_id": job.id, "state": job.state, "error": job.error}


def lexical_tokens(query: str) -> list[str]:
    return [token for token in re.findall(r"[\w'-]+", query.casefold()) if len(token) > 1]


def ask(session: Session, namespace: Namespace, question: str, mode: str, settings: Settings) -> dict[str, Any]:
    relevant_memories = active_memories(session, namespace.id, question)
    corrected_memories = [memory for memory in relevant_memories if memory.supersedes_id]
    blocked_sources = suppressed_source_ids(session, namespace.id, question)
    blocked_sources.difference_update(memory.source_id for memory in corrected_memories)
    candidates = search_sources(
        session,
        namespace.id,
        question,
        settings,
        excluded_source_ids=blocked_sources,
    )
    evidence = [candidate.source for candidate in candidates[:8]]
    if corrected_memories:
        corrected_sources = session.scalars(
            select(Source).where(
                Source.id.in_([memory.source_id for memory in corrected_memories]),
                Source.namespace_id == namespace.id,
                Source.eligible.is_(True),
                Source.processing_status == "ready",
            )
        ).all()
        corrected_ids = {source.id for source in corrected_sources}
        evidence = corrected_sources + [source for source in evidence if source.id not in corrected_ids]
        evidence = evidence[:8]
    revision = namespace.revision
    if not evidence:
        result = {
            "status": "insufficient_evidence", "answer": "I couldn't find supporting information in this memory.",
            "claims": [], "evidence": [], "uncertainties": ["No eligible source matched the question."],
            "coverage": {"mode": "focused", "complete": True}, "namespace_revision": revision,
        }
    else:
        bounded_evidence: list[Source] = []
        snippets: list[str] = []
        remaining_chars = settings.max_evidence_chars_total
        for source in evidence:
            full_text = source.formatted_text.strip() or source.raw_asr.strip()
            limit = min(settings.max_evidence_chars_per_source, remaining_chars)
            if limit <= 0:
                break
            if len(full_text) > limit:
                snippet = f"{full_text[: limit - 1].rstrip()}…"
            else:
                snippet = full_text
            bounded_evidence.append(source)
            snippets.append(snippet)
            remaining_chars -= len(snippet)
        evidence = bounded_evidence
        top = evidence[0]
        memory_context = [
            {
                "id": memory.id,
                "source_id": memory.source_id,
                "text": f"{memory.subject} {memory.predicate} {memory.value}",
                "scope": memory.scope,
            }
            for memory in corrected_memories
        ]
        if corrected_memories and not settings.sarvam_api_key:
            answer = "\n".join(
                f"Based on your correction: {memory.subject} {memory.predicate} {memory.value}."
                for memory in corrected_memories
            )
            result = {"status": "answered", "answer": answer, "generation": "controlled_memory"}
        else:
            generated = synthesize(
                settings,
                question,
                [{"id": source.id, "text": text} for source, text in zip(evidence, snippets)],
                mode=mode,
                memory_context=memory_context,
            )
            if generated.error and generated.error != "model_not_configured":
                result = {"status": "failed", "answer": "The model provider is unavailable. Your source history remains available below."}
            else:
                if generated.text:
                    answer = generated.text
                elif mode == "draft":
                    answer = "\n".join(f"• {snippet}" for snippet in snippets[:3])
                else:
                    answer = snippets[0]
                result = {
                    "status": "answered", "answer": answer,
                    "generation": "sarvam" if generated.text else "grounded_replay",
                    "provider": {"model": generated.model, "latency_ms": generated.latency_ms, "usage": generated.usage} if generated.text else None,
                }
        if mode == "draft":
            result["draft_text"] = result["answer"]
        result.update({
            "claims": [{
                "text": result["answer"],
                "evidence_ids": [memory.id for memory in corrected_memories] or [top.id],
                "kind": "user_corrected_memory" if corrected_memories else "attributed_record",
            }],
            "evidence": [{"id": source.id, "external_id": source.external_id, "text": snippet,
                          "occurred_at": source.occurred_at.isoformat() if source.occurred_at else None}
                         for source, snippet in zip(evidence, snippets)],
            "applied_preferences": [{"id": item.id, "value": item.value, "scope": item.scope} for item in relevant_memories if item.kind == "preference"],
            "uncertainties": ["Answers are grounded in the listed source evidence. Review source wording for high-stakes decisions."],
            "coverage": {"mode": "focused", "complete": len(candidates) <= 8}, "namespace_revision": revision,
            "retrieval": [{"source_id": candidate.source.id, "external_id": candidate.source.external_id,
                           "lexical_rank": candidate.lexical_rank, "dense_rank": candidate.dense_rank,
                           "rrf_score": round(candidate.score, 7)} for candidate in candidates[:30]],
        })
    result["trace_id"] = ident("query")
    run = QueryRun(id=result["trace_id"], namespace_id=namespace.id, question=question, status=result["status"],
                   answer_json=json.dumps(result, ensure_ascii=False), revision=revision)
    session.add(run)
    session.commit()
    return result


def source_payload(source: Source) -> dict[str, Any]:
    return {"id": source.id, "external_id": source.external_id, "raw_asr": source.raw_asr,
            "formatted_text": source.formatted_text, "occurred_at": source.occurred_at,
            "timezone": source.timezone_name, "app": source.app,
            "context": json.loads(source.context_json),
            "processing_status": source.processing_status, "eligible": source.eligible,
            "source_version": source.source_version}


def database_status(session: Session) -> dict[str, Any]:
    session.execute(text("SELECT 1"))
    return {"database": "ready"}
