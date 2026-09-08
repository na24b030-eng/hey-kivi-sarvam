"""Transparent hybrid candidate ranking for the initial local corpus scale."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Embedding, Memory, Source, SourceChunk
from .embeddings import encode_query, vector_from_blob
from .settings import Settings


def tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[\w'-]+", value.casefold()) if len(token) > 1]


def char_ngrams(value: str) -> set[str]:
    padded = f"  {value.casefold()}  "
    return {padded[index:index + 3] for index in range(max(0, len(padded) - 2))}


@dataclass(frozen=True)
class Candidate:
    source: Source
    lexical_rank: int | None
    dense_rank: int | None
    score: float


def search_sources(session: Session, namespace_id: str, question: str, settings: Settings, limit: int = 30) -> list[Candidate]:
    """Fuse lexical ranking with persisted E5 vectors, or an explicit local fallback.

    When vectors are absent the deterministic character n-gram score keeps a local
    reviewer run usable. It is a fallback, never represented as multilingual E5.
    """
    query_terms, query_grams = tokens(question), char_ngrams(question)
    sources = session.scalars(select(Source).where(Source.namespace_id == namespace_id, Source.eligible.is_(True))).all()
    lexical: list[tuple[float, Source]] = []
    dense: list[tuple[float, Source]] = []
    query_vector = encode_query(settings, question)
    dense_scores: dict[str, float] = {}
    if query_vector is not None:
        rows = session.execute(
            select(Embedding, SourceChunk, Source)
            .join(SourceChunk, Embedding.source_chunk_id == SourceChunk.id)
            .join(Source, SourceChunk.source_id == Source.id)
            .where(Source.namespace_id == namespace_id, Source.eligible.is_(True))
        ).all()
        for embedding, _chunk, source in rows:
            vector = vector_from_blob(embedding.vector)
            if vector.size == query_vector.size:
                dense_scores[source.id] = max(dense_scores.get(source.id, -1.0), float(np.dot(query_vector, vector)))
    for source in sources:
        material = f"{source.formatted_text} {source.raw_asr}".casefold()
        lexical_score = sum(material.count(term) for term in query_terms)
        source_grams = char_ngrams(material)
        union = len(query_grams | source_grams)
        uses_embedding = source.id in dense_scores
        dense_score = dense_scores.get(source.id, len(query_grams & source_grams) / union if union else 0.0)
        if lexical_score:
            lexical.append((lexical_score, source))
        # Trigram overlap is only an offline safety-net. Incidental overlap in
        # common words must not turn an unknown question into a cited answer.
        if dense_score >= (0.0 if uses_embedding else 0.10):
            dense.append((dense_score, source))
    lexical.sort(key=lambda row: row[0], reverse=True)
    dense.sort(key=lambda row: row[0], reverse=True)
    ranks: dict[str, list[int | None]] = defaultdict(lambda: [None, None])
    source_by_id = {source.id: source for source in sources}
    for rank, (_, source) in enumerate(lexical[:limit], start=1): ranks[source.id][0] = rank
    for rank, (_, source) in enumerate(dense[:limit], start=1): ranks[source.id][1] = rank
    ranked = []
    for source_id, (lex_rank, dense_rank) in ranks.items():
        score = sum(1 / (60 + rank) for rank in (lex_rank, dense_rank) if rank is not None)
        ranked.append(Candidate(source_by_id[source_id], lex_rank, dense_rank, score))
    return sorted(ranked, key=lambda item: (item.score, item.source.occurred_at or item.source.ingested_at), reverse=True)[:limit]


def active_memories(session: Session, namespace_id: str, question: str) -> list[Memory]:
    query = set(tokens(question))
    memories = session.scalars(select(Memory).where(Memory.namespace_id == namespace_id, Memory.state == "active")).all()
    return [memory for memory in memories if query & set(tokens(f"{memory.subject} {memory.predicate} {memory.value}"))]
