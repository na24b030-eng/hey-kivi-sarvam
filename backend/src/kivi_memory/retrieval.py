"""Transparent hybrid candidate ranking for the initial local corpus scale."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Embedding, Entity, EntityAlias, EntityMention, Memory, Source, SourceChunk
from .embeddings import encode_passage, encode_query, vector_from_blob
from .settings import Settings

STOPWORDS = {
    "a", "about", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "how", "i", "in", "is", "it", "of", "on", "or", "that", "the", "this",
    "to", "was", "what", "when", "where", "which", "who", "why", "will", "with",
}


def tokens(value: str) -> list[str]:
    found: list[str] = []
    for token in re.findall(r"[\w'-]+", value.casefold()):
        if len(token) <= 1:
            continue
        found.append(token)
        # A tiny deterministic plural variant improves control matching for
        # pairs such as "email"/"emails" without pretending to be a full NLP stemmer.
        if token.isascii() and token.endswith("s") and len(token) > 4:
            found.append(token[:-1])
    return found


def char_ngrams(value: str) -> set[str]:
    padded = f"  {value.casefold()}  "
    return {padded[index:index + 3] for index in range(max(0, len(padded) - 2))}


@dataclass(frozen=True)
class Candidate:
    source: Source
    lexical_rank: int | None
    dense_rank: int | None
    score: float
    retrieval_mode: str | None = None


def search_sources(
    session: Session,
    namespace_id: str,
    question: str,
    settings: Settings,
    limit: int = 30,
    excluded_source_ids: set[str] | None = None,
) -> list[Candidate]:
    """Fuse lexical ranking with persisted E5 vectors, or an explicit local fallback.

    When vectors are absent the deterministic character n-gram score keeps a local
    reviewer run usable. It is a fallback, never represented as multilingual E5.
    """
    all_query_terms = tokens(question)
    query_terms = [t for t in all_query_terms if t not in STOPWORDS] or all_query_terms
    query_grams = char_ngrams(question)
    excluded_source_ids = excluded_source_ids or set()
    sources = session.scalars(
        select(Source).where(
            Source.namespace_id == namespace_id,
            Source.eligible.is_(True),
            Source.processing_status == "ready",
        )
    ).all()
    sources = [source for source in sources if source.id not in excluded_source_ids]
    lexical: list[tuple[float, Source]] = []
    dense: list[tuple[float, Source]] = []
    query_vector = encode_query(settings, question)
    dense_scores: dict[str, float] = {}
    dense_modes: dict[str, str] = {}
    if query_vector is not None:
        rows = session.execute(
            select(Embedding, SourceChunk, Source)
            .join(SourceChunk, Embedding.source_chunk_id == SourceChunk.id)
            .join(Source, SourceChunk.source_id == Source.id)
            .where(
                Source.namespace_id == namespace_id,
                Source.eligible.is_(True),
                Source.processing_status == "ready",
                Embedding.model == settings.embedding_model,
            )
        ).all()
        for embedding, _chunk, source in rows:
            if source.id in excluded_source_ids:
                continue
            vector = vector_from_blob(embedding.vector)
            if vector.size == query_vector.size:
                sim = float(np.dot(query_vector, vector))
                if sim > dense_scores.get(source.id, -1.0):
                    dense_scores[source.id] = sim
                    dense_modes[source.id] = "embedding"
    for source in sources:
        material = f"{source.formatted_text} {source.raw_asr}".casefold()
        mat_tokens = tokens(material)
        token_counts: dict[str, int] = defaultdict(int)
        for t in mat_tokens:
            token_counts[t] += 1
        lexical_score = sum(token_counts[term] for term in query_terms)
        if lexical_score:
            lexical.append((float(lexical_score), source))

        if source.id in dense_scores:
            d_score = dense_scores[source.id]
            if d_score >= settings.embedding_min_similarity:
                dense.append((d_score, source))
        else:
            source_grams = char_ngrams(material)
            union = len(query_grams | source_grams)
            trigram_score = len(query_grams & source_grams) / union if union else 0.0
            if trigram_score >= 0.30:
                dense.append((trigram_score, source))
                dense_modes[source.id] = "trigram_fallback"

    lexical.sort(key=lambda row: row[0], reverse=True)
    dense.sort(key=lambda row: row[0], reverse=True)
    ranks: dict[str, list[int | None]] = defaultdict(lambda: [None, None])
    source_by_id = {source.id: source for source in sources}
    for rank, (_, source) in enumerate(lexical[:limit], start=1):
        ranks[source.id][0] = rank
    for rank, (_, source) in enumerate(dense[:limit], start=1):
        ranks[source.id][1] = rank
    ranked: list[Candidate] = []
    for source_id, (lex_rank, dense_rank) in ranks.items():
        score = sum(1 / (60 + rank) for rank in (lex_rank, dense_rank) if rank is not None)
        mode = dense_modes.get(source_id)
        ranked.append(Candidate(source_by_id[source_id], lex_rank, dense_rank, score, mode))

    def _sort_key(item: Candidate):
        dt = item.source.occurred_at or item.source.ingested_at
        if dt is not None and dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (item.score, dt or datetime.min.replace(tzinfo=UTC))

    sorted_candidates = sorted(ranked, key=_sort_key, reverse=True)

    # Deduplicate candidates by content digest to collapse repeated transcripts
    seen_groups: set[str] = set()
    deduped: list[Candidate] = []
    for candidate in sorted_candidates:
        group_id = hashlib.sha256(
            f"{candidate.source.formatted_text or candidate.source.raw_asr}".strip().casefold().encode()
        ).hexdigest()[:24]
        if group_id in seen_groups:
            continue
        seen_groups.add(group_id)
        deduped.append(candidate)

    return deduped[:limit]


def active_memories(session: Session, namespace_id: str, question: str) -> list[Memory]:
    raw_terms = tokens(question)
    query = set(raw_terms) - STOPWORDS or set(raw_terms)
    memories = session.scalars(select(Memory).where(Memory.namespace_id == namespace_id, Memory.state == "active")).all()
    matched: list[Memory] = []
    for memory in memories:
        mem_tokens = set(tokens(f"{memory.subject} {memory.predicate} {memory.value}")) - STOPWORDS
        if query & mem_tokens:
            matched.append(memory)
    return matched


def suppressed_source_ids(
    session: Session,
    namespace_id: str,
    question: str,
    settings: Settings | None = None,
) -> set[str]:
    """Return sources whose matching promoted memory was explicitly suppressed.

    Source history remains inspectable. The exclusion only prevents a suppressed
    memory statement from being replayed as an answer to a matching question.
    """
    raw_terms = tokens(question)
    query = set(raw_terms) - STOPWORDS or set(raw_terms)
    memories = session.scalars(
        select(Memory).where(
            Memory.namespace_id == namespace_id,
            Memory.state == "suppressed",
        )
    ).all()
    blocked: set[str] = set()
    query_vector = encode_query(settings, question) if settings else None
    for memory in memories:
        mem_text = f"{memory.subject} {memory.predicate} {memory.value}"
        mem_tokens = set(tokens(mem_text)) - STOPWORDS
        if query & mem_tokens:
            blocked.add(memory.source_id)
            continue
        if query_vector is not None and settings:
            encoded = encode_passage(settings, mem_text)
            if encoded:
                vec = vector_from_blob(encoded[0])
                if vec.size == query_vector.size and float(np.dot(query_vector, vec)) >= settings.embedding_min_similarity:
                    blocked.add(memory.source_id)
    return blocked


# ---------------------------------------------------------------------------
# Pillar 4: Multi-hop graph expansion
# ---------------------------------------------------------------------------

def extract_query_entities(session: Session, namespace_id: str, question: str) -> list[Entity]:
    """Match question tokens against known entity aliases and canonical names."""
    query_tokens = set(tokens(question)) - STOPWORDS
    if not query_tokens:
        return []

    # Fetch all aliases for this namespace via a join
    alias_rows = session.execute(
        select(EntityAlias, Entity)
        .join(Entity, EntityAlias.entity_id == Entity.id)
        .where(Entity.namespace_id == namespace_id)
    ).all()

    matched_entities: dict[str, Entity] = {}
    for alias, entity in alias_rows:
        alias_tokens = set(tokens(alias.alias)) - STOPWORDS
        if query_tokens & alias_tokens:
            matched_entities[entity.id] = entity

    # Also check canonical names directly
    if not matched_entities:
        entities = session.scalars(
            select(Entity).where(Entity.namespace_id == namespace_id)
        ).all()
        for entity in entities:
            name_tokens = set(tokens(entity.canonical_name)) - STOPWORDS
            if query_tokens & name_tokens:
                matched_entities[entity.id] = entity

    return list(matched_entities.values())


def multi_hop_expand(
    session: Session,
    namespace_id: str,
    hop1_sources: list[Source],
    question: str,
    settings: Settings,
    max_bridge: int = 4,
) -> list[Source]:
    """Find bridge sources that share entities with hop-1 results.

    This enables answering questions like "Is the Harbor lead available Friday?"
    when Source A says "Dev leads Harbor" and Source B says "Dev is on leave Friday"
    — Source B wouldn't match "Harbor" directly but shares the entity "Dev".
    """
    if not hop1_sources:
        return []

    hop1_ids = {s.id for s in hop1_sources}

    # Collect all entity IDs mentioned in hop-1 sources
    hop1_entity_ids: set[str] = set()
    for source in hop1_sources:
        mentions = session.scalars(
            select(EntityMention).where(EntityMention.source_id == source.id)
        ).all()
        hop1_entity_ids.update(m.entity_id for m in mentions)

    # Also add entities found directly in the question
    query_entities = extract_query_entities(session, namespace_id, question)
    hop1_entity_ids.update(e.id for e in query_entities)

    if not hop1_entity_ids:
        return []

    # Find sources mentioning the same entities but not in hop-1
    bridge_mentions = session.scalars(
        select(EntityMention).where(
            EntityMention.entity_id.in_(hop1_entity_ids),
            EntityMention.source_id.notin_(hop1_ids),
        )
    ).all()

    # Score bridge sources by entity overlap count
    bridge_scores: dict[str, int] = defaultdict(int)
    for m in bridge_mentions:
        bridge_scores[m.source_id] += 1

    if not bridge_scores:
        return []

    # Fetch and filter eligible bridge sources
    bridge_source_ids = sorted(bridge_scores, key=bridge_scores.get, reverse=True)[:max_bridge * 2]  # type: ignore[arg-type]
    bridge_sources = session.scalars(
        select(Source).where(
            Source.id.in_(bridge_source_ids),
            Source.namespace_id == namespace_id,
            Source.eligible.is_(True),
            Source.processing_status == "ready",
        )
    ).all()

    # Sort by entity overlap score, then recency
    def _bridge_key(s: Source) -> tuple[int, datetime]:
        dt = s.occurred_at or s.ingested_at
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (bridge_scores.get(s.id, 0), dt or datetime.min.replace(tzinfo=UTC))

    bridge_sources.sort(key=_bridge_key, reverse=True)
    return bridge_sources[:max_bridge]


# ---------------------------------------------------------------------------
# Pillar 4: Time-based decay scoring
# ---------------------------------------------------------------------------

def apply_decay(candidates: list[Candidate], now_utc: datetime) -> list[Candidate]:
    """Apply time-based decay multipliers to candidate scores.

    - Very old content (>30 days) gets mild exponential decay
    - Scheduled memories past their expiry date get strong decay
    - Preferences and recent facts are unaffected
    """
    decayed: list[Candidate] = []
    for c in candidates:
        multiplier = 1.0
        source_time = c.source.occurred_at or c.source.ingested_at
        if source_time:
            if source_time.tzinfo is None:
                source_time = source_time.replace(tzinfo=UTC)
            age_days = (now_utc - source_time).total_seconds() / 86400
            # Mild decay for very old general content
            if age_days > 30:
                multiplier *= max(0.5, 2 ** (-(age_days - 30) / 90))

        decayed.append(Candidate(
            source=c.source,
            lexical_rank=c.lexical_rank,
            dense_rank=c.dense_rank,
            score=c.score * multiplier,
            retrieval_mode=c.retrieval_mode,
        ))

    decayed.sort(key=lambda x: x.score, reverse=True)
    return decayed

