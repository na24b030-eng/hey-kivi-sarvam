"""Reproducible local evaluation runner for frozen JSONL cases."""
from __future__ import annotations

import hashlib
import json
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session

from .db import Namespace
from .services import ask
from .settings import Settings


def run_suite(session: Session, namespace: Namespace, suite_path: Path, output_path: Path, settings: Settings) -> dict[str, Any]:
    cases = [json.loads(line) for line in suite_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    results: list[dict[str, Any]] = []
    for case in cases:
        started = perf_counter()
        answer = ask(session, namespace, case["question"], case.get("mode", "answer"), settings)
        expected = case.get("expected_status", "answered")
        returned_sources = {item["external_id"] for item in answer.get("evidence", [])}
        required_source = case.get("required_external_id")
        source_passed = required_source is None or required_source in returned_sources
        expected_text = case.get("expected_text")
        text_passed = expected_text is None or expected_text in answer.get("answer", "") or any(expected_text in item.get("text", "") for item in answer.get("evidence", []))
        results.append({"id": case["id"], "category": case.get("category", "unspecified"), "expected_status": expected,
                        "actual_status": answer["status"], "required_external_id": required_source,
                        "source_passed": source_passed, "text_passed": text_passed,
                        "passed": answer["status"] == expected and source_passed and text_passed,
                        "latency_ms": round((perf_counter() - started) * 1000, 2), "trace_id": answer["trace_id"],
                        "provider": answer.get("provider")})
    passed = sum(1 for result in results if result["passed"])
    categories: dict[str, dict[str, int]] = defaultdict(lambda: {"cases": 0, "passed": 0})
    for result in results:
        summary = categories[result["category"]]
        summary["cases"] += 1
        summary["passed"] += int(result["passed"])
    provider_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "answered_with_provider": 0}
    for result in results:
        provider = result.get("provider") or {}
        usage = provider.get("usage") or {}
        if provider:
            provider_usage["answered_with_provider"] += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if isinstance(usage.get(key), int):
                provider_usage[key] += usage[key]
    latencies = [result["latency_ms"] for result in results]
    database_path = settings.app_data_dir / "memory.sqlite3" if settings.database_url is None else None
    report = {
        "created_at": datetime.now(UTC).isoformat(), "suite": str(suite_path),
        "suite_sha256": hashlib.sha256(suite_path.read_bytes()).hexdigest(), "case_count": len(results),
        "passed": passed, "evidence_accuracy": passed / len(results) if results else 0,
        "category_summary": dict(sorted(categories.items())),
        "latency_ms": {"p50": round(statistics.median(latencies), 2) if latencies else None,
                       "p95": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 2) if latencies else None},
        "database_bytes": database_path.stat().st_size if database_path and database_path.exists() else None,
        "embedding_model": settings.embedding_model, "provider_configured": bool(settings.sarvam_api_key),
        "provider_usage": provider_usage if provider_usage["answered_with_provider"] else "unknown_without_a_live_provider_response",
        "results": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
