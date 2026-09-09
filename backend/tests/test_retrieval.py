import json
from pathlib import Path

from fastapi.testclient import TestClient

from kivi_memory.api import create_app
from kivi_memory.settings import Settings


def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(Settings(app_data_dir=tmp_path, sarvam_api_key="")))


def test_common_words_do_not_cause_false_positive_retrieval(tmp_path: Path):
    app = client(tmp_path)
    ns = app.post("/api/namespaces", json={"name": "Stopwords"}).json()
    record = json.dumps({
        "schema_version": 1,
        "id": "office-color",
        "raw_asr": "the office is painted blue",
        "formatted_text": "The office is painted blue.",
    })
    app.post(f"/api/namespaces/{ns['id']}/imports", json={"jsonl": record})
    app.post("/api/worker/drain")

    # Common words 'what', 'is', 'the', 'of' must not retrieve 'The office is painted blue'
    res = app.post(
        f"/api/namespaces/{ns['id']}/ask",
        json={"question": "What is the capital of Peru?"},
    ).json()

    assert res["status"] == "insufficient_evidence"
    assert len(res["evidence"]) == 0


def test_whole_word_matching_not_substring(tmp_path: Path):
    app = client(tmp_path)
    ns = app.post("/api/namespaces", json={"name": "Substrings"}).json()
    record = json.dumps({
        "schema_version": 1,
        "id": "order-doc",
        "raw_asr": "we received a new order",
        "formatted_text": "We received a new order.",
    })
    app.post(f"/api/namespaces/{ns['id']}/imports", json={"jsonl": record})
    app.post("/api/worker/drain")

    # Term 'or' should not match substring inside 'order'
    res = app.post(
        f"/api/namespaces/{ns['id']}/ask",
        json={"question": "Is or permitted?"},
    ).json()

    assert res["status"] == "insufficient_evidence"


def test_duplicate_group_deduplication(tmp_path: Path):
    app = client(tmp_path)
    ns = app.post("/api/namespaces", json={"name": "Duplicates"}).json()
    rec1 = json.dumps({
        "schema_version": 1,
        "id": "doc-1",
        "raw_asr": "lantern launch monday",
        "formatted_text": "Lantern launches Monday.",
    })
    rec2 = json.dumps({
        "schema_version": 1,
        "id": "doc-2",
        "raw_asr": "lantern launch monday",
        "formatted_text": "Lantern launches Monday.",
    })
    app.post(f"/api/namespaces/{ns['id']}/imports", json={"jsonl": f"{rec1}\n{rec2}"})
    app.post("/api/worker/drain")

    res = app.post(
        f"/api/namespaces/{ns['id']}/ask",
        json={"question": "When does Lantern launch?"},
    ).json()

    assert res["status"] == "answered"
    # Collapsed by duplicate group so evidence only lists one copy
    assert len(res["evidence"]) == 1


def test_retrieval_mode_in_trace(tmp_path: Path):
    app = client(tmp_path)
    ns = app.post("/api/namespaces", json={"name": "Trace"}).json()
    rec = json.dumps({
        "schema_version": 1,
        "id": "trace-doc",
        "raw_asr": "lantern launch monday",
        "formatted_text": "Lantern launches Monday.",
    })
    app.post(f"/api/namespaces/{ns['id']}/imports", json={"jsonl": rec})
    app.post("/api/worker/drain")

    res = app.post(
        f"/api/namespaces/{ns['id']}/ask",
        json={"question": "When does Lantern launch?"},
    ).json()

    assert "retrieval" in res
    assert len(res["retrieval"]) > 0
    first = res["retrieval"][0]
    assert "retrieval_mode" in first
