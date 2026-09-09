"""Tests for Pillar 4: Semantic Intelligence & Multi-Hop Reasoning."""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from kivi_memory.api import create_app
from kivi_memory.db import (
    Entity,
    EntityAlias,
    EntityMention,
    EntityRelation,
    Memory,
    Source,
)
from kivi_memory.retrieval import (
    Candidate,
    apply_decay,
    extract_query_entities,
    multi_hop_expand,
    tokens,
)
from kivi_memory.semantic import (
    Contradiction,
    DecayHint,
    ExtractedEntity,
    ExtractedRelation,
    SemanticGraph,
    classify_decay,
    detect_contradictions,
    merge_entities,
    persist_mentions,
    persist_relations,
    resolve_coreferences,
    Coreference,
)
from kivi_memory.settings import Settings


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(Settings(app_data_dir=tmp_path, sarvam_api_key="")))


def _ingest(app: TestClient, ns_id: str, external_id: str, raw: str, formatted: str, occurred_at: str | None = None):
    """Helper to import a single record and drain."""
    record: dict = {
        "schema_version": 1,
        "id": external_id,
        "raw_asr": raw,
        "formatted_text": formatted,
    }
    if occurred_at:
        record["occurred_at"] = occurred_at
    app.post(f"/api/namespaces/{ns_id}/imports", json={"jsonl": json.dumps(record)})
    app.post("/api/worker/drain")


# ──────────────────────────────────────────────────────────────────────
# 1. Coreference Resolution
# ──────────────────────────────────────────────────────────────────────

def test_coreference_resolution_replaces_pronouns():
    sentences = [
        "Dev leads the Harbor project.",
        "He said the launch is on Friday.",
        "She will review the proposal.",
    ]
    corefs = [
        Coreference(pronoun="He", resolved_entity="Dev", sentence_index=1),
        Coreference(pronoun="She", resolved_entity="Priya", sentence_index=2),
    ]
    resolved = resolve_coreferences(sentences, corefs)
    assert "Dev said the launch is on Friday." == resolved[1]
    assert "Priya will review the proposal." == resolved[2]
    assert resolved[0] == sentences[0]  # unchanged


def test_coreference_resolution_empty():
    sentences = ["Hello world."]
    assert resolve_coreferences(sentences, []) == sentences


# ──────────────────────────────────────────────────────────────────────
# 2. Entity Merging / Deduplication
# ──────────────────────────────────────────────────────────────────────

def test_entity_deduplication(tmp_path: Path):
    """Two sources mentioning the same entity name should merge to one Entity record."""
    app = _client(tmp_path)
    ns = app.post("/api/namespaces", json={"name": "EntityDedup"}).json()

    # Need a real session for direct DB operations
    from kivi_memory.db import build_session_factory
    settings = Settings(app_data_dir=tmp_path, sarvam_api_key="")
    factory, _ = build_session_factory(settings)
    session = factory()

    from kivi_memory.services import ensure_namespace, ident
    namespace = ensure_namespace(session, "EntityDedup")

    # Create a dummy source for provenance
    src = Source(
        id=ident("src"), namespace_id=namespace.id, external_id="test-src",
        source_version=1, raw_asr="test", formatted_text="test",
        checksum="abc", processing_status="ready",
    )
    session.add(src)
    session.flush()

    entities_1 = [ExtractedEntity(name="Dev Sharma", entity_type="person", aliases=["Dev"])]
    entities_2 = [ExtractedEntity(name="Dev Sharma", entity_type="person", aliases=["Mr. Sharma"])]

    map1 = merge_entities(session, namespace.id, src.id, entities_1)
    map2 = merge_entities(session, namespace.id, src.id, entities_2)

    # Same entity ID
    assert map1["Dev Sharma"] == map2["Dev Sharma"]

    # Check aliases
    from sqlalchemy import select
    aliases = session.scalars(
        select(EntityAlias).where(EntityAlias.entity_id == map1["Dev Sharma"])
    ).all()
    alias_texts = {a.alias for a in aliases}
    assert "Dev Sharma" in alias_texts
    assert "Dev" in alias_texts
    assert "Mr. Sharma" in alias_texts
    session.close()


# ──────────────────────────────────────────────────────────────────────
# 3. Decay Classification
# ──────────────────────────────────────────────────────────────────────

def test_decay_classify_preference():
    dc, exp = classify_decay("preference", "preference", "dark mode")
    assert dc == "permanent"
    assert exp is None


def test_decay_classify_schedule():
    dc, exp = classify_decay("fact", "schedule", "October 15")
    assert dc == "scheduled"


def test_decay_classify_regular_fact():
    dc, exp = classify_decay("fact", "is", "the capital of France")
    assert dc == "permanent"
    assert exp is None


def test_decay_classify_meeting():
    dc, exp = classify_decay("fact", "is", "meeting at 3pm")
    assert dc == "scheduled"


# ──────────────────────────────────────────────────────────────────────
# 4. Time-based Decay Scoring
# ──────────────────────────────────────────────────────────────────────

def test_apply_decay_recent_unchanged():
    """Recent sources should have no decay applied."""
    now = datetime.now(UTC)
    src = Source(
        id="src_test",
        namespace_id="ns_test",
        external_id="ext_test",
        checksum="hash",
        raw_asr="",
        formatted_text="",
        occurred_at=now - timedelta(days=5),
        ingested_at=now - timedelta(days=5),
    )

    candidate = Candidate(source=src, lexical_rank=1, dense_rank=1, score=1.0, retrieval_mode="embedding")
    result = apply_decay([candidate], now)
    assert result[0].score == 1.0  # No decay for recent content


def test_apply_decay_old_content_decays():
    """Content older than 30 days should have reduced score."""
    now = datetime.now(UTC)
    src = Source(
        id="src_old",
        namespace_id="ns_test",
        external_id="ext_test",
        checksum="hash",
        raw_asr="",
        formatted_text="",
        occurred_at=now - timedelta(days=60),
        ingested_at=now - timedelta(days=60),
    )

    candidate = Candidate(source=src, lexical_rank=1, dense_rank=1, score=1.0, retrieval_mode="embedding")
    result = apply_decay([candidate], now)
    assert result[0].score < 1.0
    assert result[0].score >= 0.5  # Minimum multiplier is 0.5


# ──────────────────────────────────────────────────────────────────────
# 5. Contradiction Detection
# ──────────────────────────────────────────────────────────────────────

def test_contradiction_detection(tmp_path: Path):
    """Two sources with conflicting values for the same entity+predicate should be flagged."""
    from kivi_memory.db import build_session_factory
    settings = Settings(app_data_dir=tmp_path, sarvam_api_key="")
    factory, _ = build_session_factory(settings)
    session = factory()

    from kivi_memory.services import ensure_namespace, ident
    namespace = ensure_namespace(session, "ContraTest")
    now_time = datetime.now(UTC)

    # Source 1: Meeting at 3pm
    src1 = Source(
        id=ident("src"), namespace_id=namespace.id, external_id="meet-1",
        source_version=1, raw_asr="meeting at 3pm", formatted_text="Meeting at 3pm.",
        checksum="abc1", processing_status="ready",
        occurred_at=now_time,
    )
    session.add(src1)
    session.flush()

    # Source 2: Meeting at 4pm (30 minutes later)
    src2 = Source(
        id=ident("src"), namespace_id=namespace.id, external_id="meet-2",
        source_version=1, raw_asr="meeting at 4pm", formatted_text="Meeting at 4pm.",
        checksum="abc2", processing_status="ready",
        occurred_at=now_time + timedelta(minutes=30),
    )
    session.add(src2)
    session.flush()

    # Create shared entity
    ent = Entity(id=ident("ent"), namespace_id=namespace.id, canonical_name="team meeting", entity_type="general")
    session.add(ent)
    session.flush()

    entity_map = {"team meeting": ent.id}

    # Insert first relation
    rel1 = [ExtractedRelation(subject="team meeting", predicate="scheduled_at", obj="3pm", confidence=1.0)]
    persist_relations(session, namespace.id, src1.id, entity_map, rel1)

    # Detect contradictions for second relation
    rel2 = [ExtractedRelation(subject="team meeting", predicate="scheduled_at", obj="4pm", confidence=1.0)]
    contradictions = detect_contradictions(session, namespace.id, src2.id, entity_map, rel2, time_window_hours=1.0)

    assert len(contradictions) == 1
    assert contradictions[0].old_value == "3pm"
    assert contradictions[0].new_value == "4pm"
    assert contradictions[0].temporal_gap_hours < 1.0
    session.close()


# ──────────────────────────────────────────────────────────────────────
# 6. Multi-hop Query Entity Extraction
# ──────────────────────────────────────────────────────────────────────

def test_extract_query_entities(tmp_path: Path):
    from kivi_memory.db import build_session_factory
    settings = Settings(app_data_dir=tmp_path, sarvam_api_key="")
    factory, _ = build_session_factory(settings)
    session = factory()

    from kivi_memory.services import ensure_namespace, ident
    namespace = ensure_namespace(session, "QueryEntTest")

    src = Source(
        id=ident("src"), namespace_id=namespace.id, external_id="harbor-src",
        source_version=1, raw_asr="harbor", formatted_text="harbor",
        checksum="abc", processing_status="ready",
    )
    session.add(src)
    session.flush()

    ent = Entity(id=ident("ent"), namespace_id=namespace.id, canonical_name="harbor", entity_type="project")
    session.add(ent)
    session.flush()

    alias = EntityAlias(id=ident("alias"), entity_id=ent.id, alias="Harbor Project",
                         source_id=src.id)
    session.add(alias)
    session.flush()

    matched = extract_query_entities(session, namespace.id, "When does Harbor launch?")
    assert len(matched) == 1
    assert matched[0].canonical_name == "harbor"
    session.close()


def test_multi_hop_expand_finds_bridge_source(tmp_path: Path):
    """Bridge source sharing an entity with hop1 source is expanded into evidence."""
    from kivi_memory.db import build_session_factory
    settings = Settings(app_data_dir=tmp_path, sarvam_api_key="")
    factory, _ = build_session_factory(settings)
    session = factory()

    from kivi_memory.services import ensure_namespace, ident
    namespace = ensure_namespace(session, "MultiHopTest")

    # Source 1: Dev leads Harbor
    src1 = Source(
        id=ident("src"), namespace_id=namespace.id, external_id="src-1",
        source_version=1, raw_asr="dev leads harbor", formatted_text="Dev leads Harbor.",
        checksum="chk1", processing_status="ready", eligible=True,
    )
    session.add(src1)

    # Source 2: Dev is on leave Friday
    src2 = Source(
        id=ident("src"), namespace_id=namespace.id, external_id="src-2",
        source_version=1, raw_asr="dev is on leave friday", formatted_text="Dev is on leave Friday.",
        checksum="chk2", processing_status="ready", eligible=True,
    )
    session.add(src2)
    session.flush()

    # Shared entity: Dev
    ent_dev = Entity(id=ident("ent"), namespace_id=namespace.id, canonical_name="dev", entity_type="person")
    session.add(ent_dev)
    session.flush()

    # Mentions linking both sources to Dev
    m1 = EntityMention(id=ident("m"), entity_id=ent_dev.id, source_id=src1.id, mention_text="Dev")
    m2 = EntityMention(id=ident("m"), entity_id=ent_dev.id, source_id=src2.id, mention_text="Dev")
    session.add_all([m1, m2])
    session.flush()

    # Hop 1 only retrieved src1
    bridge = multi_hop_expand(session, namespace.id, [src1], "Is Harbor lead available Friday?", settings)
    assert len(bridge) == 1
    assert bridge[0].id == src2.id
    session.close()


# ──────────────────────────────────────────────────────────────────────
# 7. Graceful Fallback (no API key)
# ──────────────────────────────────────────────────────────────────────

def test_semantic_extraction_graceful_without_api_key():
    """When no API key is set, extraction returns None and existing pipeline works."""
    from kivi_memory.semantic import extract_entities_and_relations
    settings = Settings(sarvam_api_key="")
    result = extract_entities_and_relations(settings, "Dev leads Harbor.", "src_test")
    assert result is None


def test_existing_pipeline_works_without_semantic(tmp_path: Path):
    """Ensure the full ingestion + ask pipeline works with semantic extraction disabled."""
    app = _client(tmp_path)
    ns = app.post("/api/namespaces", json={"name": "FallbackTest"}).json()

    record = json.dumps({
        "schema_version": 1,
        "id": "launch-note",
        "raw_asr": "harbor launches on october fifteenth",
        "formatted_text": "Harbor launches on October 15.",
    })
    app.post(f"/api/namespaces/{ns['id']}/imports", json={"jsonl": record})
    drain = app.post("/api/worker/drain").json()
    assert drain["processed"] >= 1
    assert drain["failed"] == 0

    res = app.post(
        f"/api/namespaces/{ns['id']}/ask",
        json={"question": "When does Harbor launch?"},
    ).json()
    assert res["status"] == "answered"
    assert "harbor" in res["answer"].casefold() or "october" in res["answer"].casefold()


# ──────────────────────────────────────────────────────────────────────
# 8. Contradiction to_dict serialization
# ──────────────────────────────────────────────────────────────────────

def test_contradiction_serialization():
    c = Contradiction(
        subject="meeting",
        predicate="time",
        old_value="3pm",
        new_value="4pm",
        old_source_id="src_1",
        new_source_id="src_2",
        temporal_gap_hours=0.5,
    )
    d = c.to_dict()
    assert d["subject"] == "meeting"
    assert d["old_value"] == "3pm"
    assert d["new_value"] == "4pm"
    assert "message" in d
    assert "Which is correct?" in d["message"]
