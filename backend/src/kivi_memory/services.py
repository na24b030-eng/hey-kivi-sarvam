from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import defaultdict
from datetime import UTC, timedelta
from typing import Any

import numpy as np
from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.orm import Session

from .contracts import TranscriptRecord
from .db import (
    Embedding,
    Job,
    Memory,
    MemoryOperation,
    Namespace,
    QueryRun,
    Source,
    SourceChunk,
    now,
)
from .embeddings import content_hash, encode_passage, encode_query, vector_from_blob
from .generation import synthesize
from .retrieval import STOPWORDS, active_memories, search_sources, suppressed_source_ids, tokens
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

        # Convert occurred_at to UTC to preserve absolute time across platforms and SQLite
        occurred_at_utc = record.occurred_at.astimezone(UTC) if record.occurred_at else None

        source = Source(
            id=ident("src"), namespace_id=namespace.id, external_id=record.id,
            source_version=version, raw_asr=record.raw_asr, formatted_text=record.formatted_text,
            occurred_at=occurred_at_utc, timezone_name=record.timezone, app=record.app,
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
    """Conservative deterministic multi-fact extractor.

    Splits the formatted transcript into sentences to extract multiple independent
    facts or preferences, deduplicated by subject, predicate, and scope.
    """
    value = source.formatted_text.strip()
    if not value:
        return []

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", value) if s.strip()]
    candidates: list[tuple[str, str, str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    patterns = [
        (r"(?i)\b(.+?)\s+(?:launches|launch|is scheduled|scheduled)(?:\s+on|\s+for)?\s+(.+?)[.!]?$", "schedule"),
        (r"(?i)\b(?:i|we)\s+(?:prefer|like|want)\s+(.+?)[.!]?$", "preference"),
        (r"(?i)\b(.+?)\s+is\s+(.+?)[.!]?$", "is"),
    ]

    for sentence in sentences:
        lower = sentence.casefold()
        if any(token in lower for token in ("if ", "maybe", "what if", "?")):
            continue
        for pattern, predicate in patterns:
            match = re.search(pattern, sentence)
            if not match:
                continue
            if predicate == "preference":
                cand = ("preference", "user", predicate, match.group(1).strip(), "general")
            else:
                cand = ("fact", match.group(1).strip(), predicate, match.group(2).strip(), "general")
            key = (cand[1], cand[2], cand[4])
            if key not in seen:
                seen.add(key)
                candidates.append(cand)
            break

    return candidates


def process_one_job(session: Session, settings: Settings | None = None) -> dict[str, Any] | None:
    worker_id = ident("worker")
    claim_time = now()
    lease_expiry = claim_time + timedelta(minutes=2)

    claim_subquery = (
        select(Job.id)
        .where(
            or_(
                Job.state == "queued",
                and_(Job.state == "running", Job.lease_until < claim_time),
            )
        )
        .order_by(Job.created_at)
        .limit(1)
        .scalar_subquery()
    )

    claim_stmt = (
        update(Job)
        .where(Job.id == claim_subquery)
        .values(
            state="running",
            attempts=Job.attempts + 1,
            progress=5,
            lease_until=lease_expiry,
            worker_id=worker_id,
            error=None,
        )
    )
    result = session.execute(claim_stmt)
    if result.rowcount == 0:
        return None
    session.commit()

    job = session.scalar(
        select(Job).where(Job.worker_id == worker_id, Job.state == "running")
    )
    if job is None:
        return None

    try:
        source = session.get(Source, job.source_id)
        if source is None or not source.eligible:
            job.state, job.progress = "cancelled", 100
            session.commit()
            return {"job_id": job.id, "state": job.state}

        # Idempotent cleanup: remove any partial chunks from a previous failed attempt
        stale_chunks = session.scalars(select(SourceChunk).where(SourceChunk.source_id == source.id)).all()
        for c in stale_chunks:
            session.delete(c)
        if stale_chunks:
            session.flush()

        group = hashlib.sha256(normalize(source.formatted_text or source.raw_asr).encode()).hexdigest()[:24]
        for view, value in (("raw", source.raw_asr), ("formatted", source.formatted_text)):
            for ordinal, (start, end, part) in enumerate(chunk_text(value)):
                chunk = SourceChunk(id=ident("chunk"), source_id=source.id, view=view, ordinal=ordinal,
                    start_offset=start, end_offset=end, text=part, search_text=normalize(part), duplicate_group_id=group)
                session.add(chunk)
                session.flush()
                if settings:
                    encoded = encode_passage(settings, part)
                    if encoded:
                        vector, dimensions = encoded
                        session.add(Embedding(id=ident("emb"), source_chunk_id=chunk.id, model=settings.embedding_model,
                            dimensions=dimensions, vector=vector, content_hash=content_hash(part)))
        job.progress = 50

        # --- Pillar 4: Semantic entity/relation extraction ---
        is_synthetic = bool(source.external_id and source.external_id.startswith("synthetic-"))
        if not is_synthetic and settings and settings.sarvam_api_key and settings.enable_semantic_extraction:
            from .semantic import (
                detect_contradictions,
                extract_entities_and_relations,
                merge_entities,
                persist_mentions,
                persist_relations,
            )
            semantic_graph = extract_entities_and_relations(settings, source.formatted_text, source.id)
            if semantic_graph:
                entity_map = merge_entities(session, source.namespace_id, source.id, semantic_graph.entities)
                persist_mentions(session, source.id, entity_map, semantic_graph.relations)
                persist_relations(session, source.namespace_id, source.id, entity_map, semantic_graph.relations)
                # Detect contradictions and store as pending clarifications
                contradictions = detect_contradictions(
                    session, source.namespace_id, source.id,
                    entity_map, semantic_graph.relations,
                    time_window_hours=settings.contradiction_time_window_hours,
                )
                if contradictions:
                    existing_ctx = json.loads(source.context_json) if source.context_json else {}
                    existing_ctx["pending_clarifications"] = [c.to_dict() for c in contradictions]
                    source.context_json = json.dumps(existing_ctx, ensure_ascii=False)
        job.progress = 60

        # Temporal ordering: compare timestamps before superseding
        new_source_time = source.occurred_at or source.ingested_at
        if new_source_time is not None and new_source_time.tzinfo is None:
            new_source_time = new_source_time.replace(tzinfo=UTC)


        for kind, subject, predicate, value, scope in extract_memory_candidates(source):
            # Pillar 4: Classify decay for this memory
            from .semantic import classify_decay
            mem_decay_class, mem_expires_at = classify_decay(kind, predicate, value)

            older = session.scalars(select(Memory).where(
                Memory.namespace_id == source.namespace_id, Memory.subject == subject,
                Memory.predicate == predicate, Memory.scope == scope, Memory.state == "active"
            )).all()

            # Skip duplicate if identical active memory already exists
            if any(item.value == value for item in older):
                continue

            should_supersede = True
            for item in older:
                existing_source = session.get(Source, item.source_id)
                if existing_source:
                    old_source_time = existing_source.occurred_at or existing_source.ingested_at
                    if old_source_time is not None and old_source_time.tzinfo is None:
                        old_source_time = old_source_time.replace(tzinfo=UTC)
                    if new_source_time and old_source_time and new_source_time < old_source_time:
                        # New record is chronologically older than active memory
                        should_supersede = False
                        break

            if should_supersede:
                for item in older:
                    if item.value != value:
                        item.state = "superseded"
                        session.add(MemoryOperation(
                            id=ident("op"),
                            namespace_id=source.namespace_id,
                            operation_id=f"auto_supersede_{uuid.uuid4().hex[:16]}",
                            kind="auto_supersession",
                            target_id=item.id,
                            reason="newer_source_imported",
                        ))
                mem_text = f"{subject} {predicate} {value}"
                mem_vec = None
                if settings:
                    encoded_mem = encode_passage(settings, mem_text)
                    if encoded_mem:
                        mem_vec = encoded_mem[0]

                new_mem = Memory(
                    id=ident("mem"), namespace_id=source.namespace_id, kind=kind, subject=subject,
                    predicate=predicate, value=value, scope=scope, state="active", source_id=source.id,
                    decay_class=mem_decay_class, expires_at=mem_expires_at, semantic_embedding=mem_vec
                )
                session.add(new_mem)
                session.flush()
                session.add(MemoryOperation(
                    id=ident("op"),
                    namespace_id=source.namespace_id,
                    operation_id=f"auto_promote_{uuid.uuid4().hex[:16]}",
                    kind="auto_promotion",
                    target_id=new_mem.id,
                    reason="extracted_from_source",
                ))
            else:
                mem_text = f"{subject} {predicate} {value}"
                mem_vec = None
                if settings:
                    encoded_mem = encode_passage(settings, mem_text)
                    if encoded_mem:
                        mem_vec = encoded_mem[0]
                session.add(Memory(
                    id=ident("mem"), namespace_id=source.namespace_id, kind=kind, subject=subject,
                    predicate=predicate, value=value, scope=scope, state="superseded", source_id=source.id,
                    decay_class=mem_decay_class, expires_at=mem_expires_at, semantic_embedding=mem_vec
                ))

        # Stale-attempt & eligibility recheck before publishing
        current_job = session.get(Job, job.id)
        if current_job is None or current_job.worker_id != worker_id or current_job.state != "running":
            session.rollback()
            return {"job_id": job.id, "state": "abandoned_due_to_lease_loss"}

        current_source = session.get(Source, source.id)
        if current_source is None or not current_source.eligible:
            current_job.state, current_job.progress = "cancelled", 100
            session.commit()
            return {"job_id": job.id, "state": "cancelled"}

        current_source.processing_status = "ready"
        namespace = session.get(Namespace, source.namespace_id)
        if namespace:
            namespace.revision += 1
        current_job.state, current_job.progress, current_job.lease_until = "completed", 100, None
        session.commit()
        return {"job_id": job.id, "state": job.state}
    except Exception as exc:
        session.rollback()
        job = session.get(Job, job.id)
        if job is None:
            raise
        job.state, job.error, job.lease_until = "failed", str(exc)[:500], None
        source = session.get(Source, job.source_id) if job.source_id else None
        if source:
            source.processing_status = "failed"
        session.commit()
        return {"job_id": job.id, "state": job.state, "error": job.error}


def select_best_chunks(
    session: Session,
    source: Source,
    query_terms: list[str],
    query_vector: Any,
    settings: Settings,
    max_chars: int,
) -> tuple[str, bool]:
    """Select the most relevant chunks for evidence rather than simple prefix truncation."""
    chunks = session.scalars(
        select(SourceChunk)
        .where(SourceChunk.source_id == source.id)
        .order_by(SourceChunk.ordinal)
    ).all()

    full_formatted = source.formatted_text.strip()
    full_raw = source.raw_asr.strip()
    full_text = full_formatted or full_raw

    if not chunks:
        if len(full_text) > max_chars:
            return f"{full_text[:max_chars - 1].rstrip()}…", True
        return full_text, False

    formatted_chunks = [ch for ch in chunks if ch.view == "formatted"]
    raw_chunks = [ch for ch in chunks if ch.view == "raw"]

    def score_chunk(chunk: SourceChunk) -> float:
        ch_tokens = tokens(chunk.search_text)
        token_counts: dict[str, int] = defaultdict(int)
        for t in ch_tokens:
            token_counts[t] += 1
        score = float(sum(token_counts[t] for t in query_terms))
        if query_vector is not None:
            emb = session.scalar(select(Embedding).where(Embedding.source_chunk_id == chunk.id))
            if emb:
                vec = vector_from_blob(emb.vector)
                if vec.size == query_vector.size:
                    score += max(0.0, float(np.dot(query_vector, vec))) * 5.0
        return score

    fmt_scored = [(score_chunk(ch), ch) for ch in formatted_chunks]
    raw_scored = [(score_chunk(ch), ch) for ch in raw_chunks]

    # If formatted chunks matched, use formatted chunks exclusively to avoid duplicating raw dictation
    if any(s > 0 for s, _ in fmt_scored) or not raw_chunks:
        candidate_pool = fmt_scored
    elif any(s > 0 for s, _ in raw_scored):
        candidate_pool = raw_scored
    else:
        candidate_pool = fmt_scored or raw_scored

    candidate_pool.sort(key=lambda item: item[0], reverse=True)

    selected: list[SourceChunk] = []
    total_len = 0
    for _, ch in candidate_pool:
        remaining = max_chars - total_len
        if remaining <= 0:
            break
        if len(ch.text) <= remaining:
            selected.append(ch)
            total_len += len(ch.text)
        elif not selected:
            # First chunk is larger than max_chars, truncate it
            selected.append(ch)
            total_len = len(ch.text)
            break
        else:
            break

    selected.sort(key=lambda ch: (ch.view != "formatted", ch.ordinal))

    pieces = []
    for ch in selected:
        txt = ch.text.strip()
        if len(txt) > max_chars:
            txt = f"{txt[:max_chars - 1].rstrip()}…"
        pieces.append(txt)

    snippet = " […] ".join(pieces)
    if len(snippet) > max_chars:
        snippet = f"{snippet[:max_chars - 1].rstrip()}…"

    is_truncated = len(snippet) < len(full_text)
    return snippet, is_truncated


def ask(session: Session, namespace: Namespace, question: str, mode: str, settings: Settings) -> dict[str, Any]:
    raw_terms = tokens(question)
    query_terms = [t for t in raw_terms if t not in STOPWORDS] or raw_terms
    query_vector = encode_query(settings, question)

    relevant_memories = active_memories(session, namespace.id, question)
    corrected_memories = [memory for memory in relevant_memories if memory.supersedes_id]
    preference_memories = [memory for memory in relevant_memories if memory.kind == "preference"]

    blocked_sources = suppressed_source_ids(session, namespace.id, question, settings)
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

    # --- Pillar 4: Multi-hop graph expansion ---
    from .retrieval import apply_decay, multi_hop_expand
    bridge_sources = multi_hop_expand(
        session, namespace.id,
        evidence, question, settings,
        max_bridge=settings.multi_hop_bridge_limit,
    )
    existing_ids = {s.id for s in evidence}
    for src in bridge_sources:
        if src.id not in existing_ids and src.id not in blocked_sources:
            evidence.append(src)
            existing_ids.add(src.id)
    evidence = evidence[:10]  # Allow slightly more evidence for multi-hop

    # --- Pillar 4: Apply time-based decay to candidate ranking ---
    from datetime import datetime as _dt
    _now_utc = _dt.now(UTC)
    candidates = apply_decay(candidates, _now_utc)

    # --- Pillar 4: Proactive contradiction detection ---
    pending_clarifications: list[dict[str, Any]] = []
    for src in evidence:
        ctx = json.loads(src.context_json) if src.context_json else {}
        clarifications = ctx.get("pending_clarifications", [])
        for c in clarifications:
            c_subject = c.get("subject", "")
            if any(t in question.casefold() for t in tokens(c_subject) if t not in STOPWORDS):
                pending_clarifications.append(c)

    revision = namespace.revision
    has_unprocessed = bool(
        session.scalar(
            select(func.count(Source.id)).where(
                Source.namespace_id == namespace.id,
                Source.processing_status == "queued",
            )
        )
    )

    if not evidence:
        result = {
            "status": "insufficient_evidence",
            "answer": "I couldn't find supporting information in this memory.",
            "claims": [],
            "evidence": [],
            "uncertainties": ["No eligible source matched the question."],
            "coverage": {
                "mode": "focused",
                "complete": True,
                "sources_matched": len(candidates),
                "sources_used": 0,
                "has_unprocessed": has_unprocessed,
            },
            "namespace_revision": revision,
        }
    else:
        bounded_evidence: list[Source] = []
        snippets: list[str] = []
        truncation_flags: list[bool] = []
        remaining_chars = settings.max_evidence_chars_total

        for source in evidence:
            limit = min(settings.max_evidence_chars_per_source, remaining_chars)
            if limit <= 0:
                break
            snippet, is_trunc = select_best_chunks(
                session, source, query_terms, query_vector, settings, limit
            )
            bounded_evidence.append(source)
            snippets.append(snippet)
            truncation_flags.append(is_trunc)
            remaining_chars -= len(snippet)

        evidence = bounded_evidence

        # Prepare memory context for generation including both corrections and preferences
        memory_context = [
            {
                "id": memory.id,
                "source_id": memory.source_id,
                "text": f"{memory.subject} {memory.predicate} {memory.value}",
                "scope": memory.scope,
                "kind": "correction",
            }
            for memory in corrected_memories
        ] + [
            {
                "id": memory.id,
                "source_id": memory.source_id,
                "text": f"{memory.subject} {memory.predicate} {memory.value}",
                "scope": memory.scope,
                "kind": "preference",
            }
            for memory in preference_memories
            if memory.id not in {m.id for m in corrected_memories}
        ]

        if corrected_memories and not settings.sarvam_api_key:
            answer = "\n".join(
                f"Based on your correction: {memory.subject} {memory.predicate} {memory.value}."
                for memory in corrected_memories
            )
            result = {"status": "answered", "answer": answer, "generation": "controlled_memory"}
            citation_verified = True
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
                citation_verified = False
            else:
                if generated.text:
                    answer = generated.text
                    gen_kind = "sarvam"
                    # Validate citations produced by the model
                    valid_ids = {s.id for s in evidence}
                    cited_ids = set(re.findall(r"\[(?:source:)?([a-zA-Z0-9_\-]+)\]", answer))
                    matching_citations = cited_ids & valid_ids

                    if cited_ids and not matching_citations:
                        # Model hallucinated source citations completely
                        answer = "\n\n".join(snippets)
                        gen_kind = "grounded_replay"
                        citation_verified = False
                    else:
                        citation_verified = bool(matching_citations or not cited_ids)
                elif mode == "draft":
                    answer = "\n".join(f"• {snippet}" for snippet in snippets[:3])
                    gen_kind = "grounded_replay"
                    citation_verified = True
                else:
                    answer = "\n\n".join(snippets)
                    gen_kind = "grounded_replay"
                    citation_verified = True

                result = {
                    "status": "answered",
                    "answer": answer,
                    "generation": gen_kind,
                    "citation_verified": citation_verified,
                    "provider": (
                        {"model": generated.model, "latency_ms": generated.latency_ms, "usage": generated.usage}
                        if generated.text and gen_kind == "sarvam"
                        else None
                    ),
                }

        if mode == "draft":
            result["draft_text"] = result["answer"]

        # Build accurate claim attributions
        evidence_ids = [s.id for s in evidence]
        found_ids = set(re.findall(r"\[(?:source:)?([a-zA-Z0-9_\-]+)\]", result["answer"]))
        attributed_ids = [sid for sid in evidence_ids if sid in found_ids] or evidence_ids

        result.update({
            "claims": [{
                "text": result["answer"],
                "evidence_ids": [memory.id for memory in corrected_memories] or attributed_ids,
                "kind": "user_corrected_memory" if corrected_memories else "attributed_record",
            }],
            "evidence": [
                {
                    "id": source.id,
                    "external_id": source.external_id,
                    "text": snippet,
                    "truncated": is_trunc,
                    "occurred_at": source.occurred_at.isoformat() if source.occurred_at else None,
                }
                for source, snippet, is_trunc in zip(evidence, snippets, truncation_flags)
            ],
            "applied_preferences": [
                {"id": item.id, "value": item.value, "scope": item.scope}
                for item in relevant_memories if item.kind == "preference"
            ],
            "uncertainties": ["Answers are grounded in the listed source evidence. Review source wording for high-stakes decisions."],
            "coverage": {
                "mode": "focused",
                "complete": len(candidates) <= 8 and not any(truncation_flags) and not has_unprocessed,
                "sources_matched": len(candidates),
                "sources_used": len(evidence),
                "has_unprocessed": has_unprocessed,
            },
            "namespace_revision": revision,
            "retrieval": [
                {
                    "source_id": candidate.source.id,
                    "external_id": candidate.source.external_id,
                    "lexical_rank": candidate.lexical_rank,
                    "dense_rank": candidate.dense_rank,
                    "retrieval_mode": candidate.retrieval_mode,
                    "rrf_score": round(candidate.score, 7),
                }
                for candidate in candidates[:30]
            ],
        })

    # --- Pillar 4: Attach proactive clarifications if any ---
    if pending_clarifications:
        result["clarifications"] = pending_clarifications

    result["trace_id"] = ident("query")
    run = QueryRun(
        id=result["trace_id"],
        namespace_id=namespace.id,
        question=question,
        status=result["status"],
        answer_json=json.dumps(result, ensure_ascii=False),
        revision=revision,
    )
    session.add(run)
    session.commit()
    return result


def source_payload(source: Source) -> dict[str, Any]:
    return {
        "id": source.id,
        "external_id": source.external_id,
        "raw_asr": source.raw_asr,
        "formatted_text": source.formatted_text,
        "occurred_at": source.occurred_at,
        "timezone": source.timezone_name,
        "app": source.app,
        "context": json.loads(source.context_json) if source.context_json else {},
        "processing_status": source.processing_status,
        "eligible": source.eligible,
        "source_version": source.source_version,
    }


def database_status(session: Session) -> dict[str, Any]:
    session.execute(text("SELECT 1"))
    return {"database": "ready"}

