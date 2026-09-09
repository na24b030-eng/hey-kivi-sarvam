"""Pillar 4: Semantic entity/relation extraction, coreference resolution, and contradiction detection.

Uses Sarvam 105B for structured extraction when available, falls back gracefully
to the existing regex pipeline when the API is unreachable.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Entity, EntityAlias, EntityMention, EntityRelation, now
from .settings import Settings


def _ident(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ExtractedEntity:
    name: str
    entity_type: str = "general"
    aliases: list[str] = field(default_factory=list)


@dataclass
class ExtractedRelation:
    subject: str
    predicate: str
    obj: str
    confidence: float = 1.0


@dataclass
class Coreference:
    pronoun: str
    resolved_entity: str
    sentence_index: int


@dataclass
class DecayHint:
    subject: str
    predicate: str
    decay_class: str  # "permanent", "scheduled", "ephemeral"
    expires_at: str | None = None  # ISO 8601


@dataclass
class SemanticGraph:
    entities: list[ExtractedEntity] = field(default_factory=list)
    relations: list[ExtractedRelation] = field(default_factory=list)
    coreferences: list[Coreference] = field(default_factory=list)
    decay_hints: list[DecayHint] = field(default_factory=list)


@dataclass
class Contradiction:
    subject: str
    predicate: str
    old_value: str
    new_value: str
    old_source_id: str
    new_source_id: str
    temporal_gap_hours: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "predicate": self.predicate,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "old_source_id": self.old_source_id,
            "new_source_id": self.new_source_id,
            "temporal_gap_hours": round(self.temporal_gap_hours, 2),
            "message": (
                f"You mentioned \"{self.subject} {self.predicate}\" as "
                f"\"{self.old_value}\" in one note and \"{self.new_value}\" in another. "
                f"Which is correct?"
            ),
        }


# ---------------------------------------------------------------------------
# LLM-powered extraction
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT = """\
You are a precise entity and relationship extractor. Given a transcript, output a JSON object with:

1. "entities": list of {{"name": "<canonical name>", "type": "<person|org|project|date|location|product|general>", "aliases": ["<alt names>"]}}
2. "relations": list of {{"subject": "<entity name>", "predicate": "<verb/relationship>", "object": "<entity name or literal value>", "confidence": <0.0-1.0>}}
3. "coreferences": list of {{"pronoun": "<he/she/it/they/etc>", "resolved_entity": "<entity name>", "sentence_index": <0-based>}}
4. "decay_hints": list of {{"subject": "<entity>", "predicate": "<relation>", "decay_class": "<permanent|scheduled|ephemeral>", "expires_at": "<ISO8601 or null>"}}

Rules:
- Extract ALL entities mentioned (people, organizations, projects, dates, locations, products).
- For each relationship between entities, create a relation entry.
- Resolve ALL pronouns (he, she, it, they, him, her, etc.) to their antecedent entity.
- For scheduled events (meetings, appointments, deadlines), set decay_class="scheduled" and expires_at to the event datetime if determinable.
- For preferences ("I prefer", "I like"), set decay_class="permanent".
- For ephemeral observations, set decay_class="ephemeral".
- Output ONLY valid JSON, no markdown fences, no commentary.

Transcript:
{text}"""


def extract_entities_and_relations(
    settings: Settings,
    text: str,
    source_id: str,
) -> SemanticGraph | None:
    """Extract entities, relations, coreferences, and decay hints from text using Sarvam 105B.

    Returns None if extraction fails or the API is unavailable.
    """
    if not settings.sarvam_api_key or not text.strip():
        return None

    try:
        timeout = httpx.Timeout(connect=10.0, read=30.0, write=15.0, pool=10.0)
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                f"{settings.chat_base_url.rstrip('/')}/chat/completions",
                headers={"api-subscription-key": settings.sarvam_api_key},
                json={
                    "model": settings.chat_model,
                    "messages": [
                        {"role": "system", "content": "You are a structured data extractor. Output only valid JSON."},
                        {"role": "user", "content": _EXTRACTION_PROMPT.format(text=text)},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 1500,
                },
            )
            response.raise_for_status()
            payload = response.json()

        content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            return None

        # Strip markdown code fences if present
        content = re.sub(r"^```(?:json)?\s*", "", content.strip())
        content = re.sub(r"\s*```$", "", content.strip())

        data = json.loads(content)
        if not isinstance(data, dict):
            return None

        graph = SemanticGraph()

        for ent in data.get("entities", []):
            if isinstance(ent, dict) and ent.get("name"):
                graph.entities.append(ExtractedEntity(
                    name=ent["name"],
                    entity_type=ent.get("type", "general"),
                    aliases=ent.get("aliases", []),
                ))

        for rel in data.get("relations", []):
            if isinstance(rel, dict) and rel.get("subject") and rel.get("predicate"):
                graph.relations.append(ExtractedRelation(
                    subject=rel["subject"],
                    predicate=rel["predicate"],
                    obj=rel.get("object", ""),
                    confidence=min(1.0, max(0.0, float(rel.get("confidence", 1.0)))),
                ))

        for coref in data.get("coreferences", []):
            if isinstance(coref, dict) and coref.get("pronoun") and coref.get("resolved_entity"):
                graph.coreferences.append(Coreference(
                    pronoun=coref["pronoun"],
                    resolved_entity=coref["resolved_entity"],
                    sentence_index=int(coref.get("sentence_index", 0)),
                ))

        for hint in data.get("decay_hints", []):
            if isinstance(hint, dict) and hint.get("subject"):
                graph.decay_hints.append(DecayHint(
                    subject=hint["subject"],
                    predicate=hint.get("predicate", ""),
                    decay_class=hint.get("decay_class", "permanent"),
                    expires_at=hint.get("expires_at"),
                ))

        return graph

    except (httpx.RequestError, httpx.HTTPStatusError, json.JSONDecodeError, ValueError, TypeError, KeyError):
        return None


# ---------------------------------------------------------------------------
# Coreference resolution
# ---------------------------------------------------------------------------

def resolve_coreferences(sentences: list[str], coreferences: list[Coreference]) -> list[str]:
    """Replace pronouns with their resolved entity names in sentences."""
    if not coreferences:
        return sentences

    resolved = list(sentences)
    coref_by_index: dict[int, list[Coreference]] = {}
    for c in coreferences:
        coref_by_index.setdefault(c.sentence_index, []).append(c)

    for idx, sentence in enumerate(resolved):
        if idx not in coref_by_index:
            continue
        for c in coref_by_index[idx]:
            # Case-insensitive whole-word replacement of the pronoun
            pattern = re.compile(r"\b" + re.escape(c.pronoun) + r"\b", re.IGNORECASE)
            resolved[idx] = pattern.sub(c.resolved_entity, resolved[idx], count=1)

    return resolved


# ---------------------------------------------------------------------------
# Entity merging / deduplication
# ---------------------------------------------------------------------------

def merge_entities(
    session: Session,
    namespace_id: str,
    source_id: str,
    extracted: list[ExtractedEntity],
) -> dict[str, str]:
    """Merge extracted entities into the DB, deduplicating by canonical name.

    Returns a mapping of surface-form name → entity_id.
    """
    name_to_id: dict[str, str] = {}

    for ent in extracted:
        canonical = ent.name.strip().casefold()
        if not canonical:
            continue

        # Check if entity already exists in this namespace
        existing = session.scalar(
            select(Entity).where(
                Entity.namespace_id == namespace_id,
                Entity.canonical_name == canonical,
            )
        )

        if existing:
            entity_id = existing.id
        else:
            entity_id = _ident("ent")
            session.add(Entity(
                id=entity_id,
                namespace_id=namespace_id,
                canonical_name=canonical,
                entity_type=ent.entity_type,
            ))
            session.flush()

        name_to_id[ent.name] = entity_id

        # Register aliases (including the canonical name itself)
        all_aliases = [ent.name] + ent.aliases
        for alias_text in all_aliases:
            alias_clean = alias_text.strip()
            if not alias_clean:
                continue
            exists = session.scalar(
                select(EntityAlias).where(
                    EntityAlias.entity_id == entity_id,
                    EntityAlias.alias == alias_clean,
                )
            )
            if not exists:
                session.add(EntityAlias(
                    id=_ident("alias"),
                    entity_id=entity_id,
                    alias=alias_clean,
                    source_id=source_id,
                ))

    session.flush()
    return name_to_id


def persist_mentions(
    session: Session,
    source_id: str,
    entity_map: dict[str, str],
    relations: list[ExtractedRelation],
) -> None:
    """Create EntityMention records from the extracted relations."""
    seen: set[tuple[str, str]] = set()
    for rel in relations:
        eid = entity_map.get(rel.subject)
        if eid and (eid, source_id) not in seen:
            seen.add((eid, source_id))
            session.add(EntityMention(
                id=_ident("mention"),
                entity_id=eid,
                source_id=source_id,
                mention_text=rel.subject,
                role="subject",
            ))
        eid_obj = entity_map.get(rel.obj)
        if eid_obj and (eid_obj, source_id) not in seen:
            seen.add((eid_obj, source_id))
            session.add(EntityMention(
                id=_ident("mention"),
                entity_id=eid_obj,
                source_id=source_id,
                mention_text=rel.obj,
                role="object",
            ))
    session.flush()


def persist_relations(
    session: Session,
    namespace_id: str,
    source_id: str,
    entity_map: dict[str, str],
    relations: list[ExtractedRelation],
) -> None:
    """Persist entity relations into the graph."""
    for rel in relations:
        subj_id = entity_map.get(rel.subject)
        if not subj_id:
            continue

        obj_id = entity_map.get(rel.obj)
        session.add(EntityRelation(
            id=_ident("rel"),
            namespace_id=namespace_id,
            subject_entity_id=subj_id,
            predicate=rel.predicate,
            object_entity_id=obj_id,
            object_literal=rel.obj if not obj_id else None,
            source_id=source_id,
            confidence=rel.confidence,
        ))
    session.flush()


# ---------------------------------------------------------------------------
# Contradiction detection
# ---------------------------------------------------------------------------

def detect_contradictions(
    session: Session,
    namespace_id: str,
    source_id: str,
    entity_map: dict[str, str],
    relations: list[ExtractedRelation],
    time_window_hours: float = 1.0,
) -> list[Contradiction]:
    """Detect contradictions between new relations and existing ones.

    A contradiction is flagged when:
    1. Same (subject_entity, predicate) has a different object value
    2. The two sources have timestamps within time_window_hours of each other
    """
    from .db import Source  # local to avoid circular at module level

    contradictions: list[Contradiction] = []
    new_source = session.get(Source, source_id)
    new_time = None
    if new_source:
        new_time = new_source.occurred_at or new_source.ingested_at
        if new_time and new_time.tzinfo is None:
            new_time = new_time.replace(tzinfo=UTC)

    for rel in relations:
        subj_id = entity_map.get(rel.subject)
        if not subj_id:
            continue

        existing = session.scalars(
            select(EntityRelation).where(
                EntityRelation.namespace_id == namespace_id,
                EntityRelation.subject_entity_id == subj_id,
                EntityRelation.predicate == rel.predicate,
                EntityRelation.source_id != source_id,
            )
        ).all()

        for old_rel in existing:
            old_value = old_rel.object_literal or ""
            new_value = rel.obj or ""
            if old_value.casefold().strip() == new_value.casefold().strip():
                continue  # Same value, no conflict

            # Check temporal proximity
            old_source = session.get(Source, old_rel.source_id)
            if not old_source:
                continue
            old_time = old_source.occurred_at or old_source.ingested_at
            if old_time and old_time.tzinfo is None:
                old_time = old_time.replace(tzinfo=UTC)

            gap_hours = float("inf")
            if new_time and old_time:
                gap_hours = abs((new_time - old_time).total_seconds()) / 3600

            if gap_hours <= time_window_hours:
                old_entity = session.get(Entity, subj_id)
                subj_name = old_entity.canonical_name if old_entity else rel.subject
                contradictions.append(Contradiction(
                    subject=subj_name,
                    predicate=rel.predicate,
                    old_value=old_value,
                    new_value=new_value,
                    old_source_id=old_rel.source_id,
                    new_source_id=source_id,
                    temporal_gap_hours=gap_hours,
                ))

    return contradictions


# ---------------------------------------------------------------------------
# Decay classification helper (used by extract_memory_candidates fallback)
# ---------------------------------------------------------------------------

_SCHEDULE_KEYWORDS = frozenset({
    "meeting", "appointment", "call", "deadline", "launch", "scheduled",
    "event", "session", "presentation", "review", "standup", "sync",
})


def classify_decay(kind: str, predicate: str, value: str) -> tuple[str, datetime | None]:
    """Classify a memory candidate's decay properties.

    Returns (decay_class, expires_at).
    """
    if kind == "preference":
        return "permanent", None

    lower_pred = predicate.casefold()
    lower_val = value.casefold()
    combined = f"{lower_pred} {lower_val}"

    if any(kw in combined for kw in _SCHEDULE_KEYWORDS) or lower_pred == "schedule":
        # Try to parse a date from value
        expires = _try_parse_date(value)
        return "scheduled", expires

    return "permanent", None


def _try_parse_date(text: str) -> datetime | None:
    """Best-effort date extraction from free text."""
    # Common date patterns
    patterns = [
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{1,2}/\d{1,2}/\d{4})",
        r"(\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{4})",
    ]
    for pat in patterns:
        match = re.search(pat, text, re.IGNORECASE)
        if match:
            try:
                from dateutil.parser import parse as dateparse
                return dateparse(match.group(1)).replace(tzinfo=UTC)
            except Exception:
                pass
    return None
