"""Reproducible English-to-Hindi retrieval smoke test for the local E5 path."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from kivi_memory.db import build_session_factory
from kivi_memory.embeddings import model_is_cached
from kivi_memory.services import ask, ensure_namespace, import_records, process_one_job
from kivi_memory.settings import Settings

RECORDS = "\n".join(
    [
        json.dumps(
            {
                "schema_version": 1,
                "id": "hindi-dentist",
                "raw_asr": "कल सुबह दंत चिकित्सक से मिलने का समय है।",
                "formatted_text": "कल सुबह दंत चिकित्सक से मिलने का समय है।",
                "language_hints": ["hi"],
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "schema_version": 1,
                "id": "hindi-budget",
                "raw_asr": "टीम की बजट बैठक शुक्रवार दोपहर को है।",
                "formatted_text": "टीम की बजट बैठक शुक्रवार दोपहर को है।",
                "language_hints": ["hi"],
            },
            ensure_ascii=False,
        ),
    ]
)
QUESTION = "When is my dentist appointment?"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("eval/results/multilingual-embedding-report.json"),
    )
    args = parser.parse_args()
    base_settings = Settings(sarvam_api_key="")
    if not model_is_cached(base_settings):
        raise SystemExit(
            "Embedding model is not cached. Run `kivi-memory doctor --download-embedding` first."
        )

    with tempfile.TemporaryDirectory(prefix="kivi-multilingual-") as temporary:
        settings = Settings(
            app_data_dir=Path(temporary),
            embedding_cache_dir=base_settings.embeddings_dir,
            sarvam_api_key="",
        )
        factory, engine = build_session_factory(settings)
        try:
            with factory() as session:
                namespace = ensure_namespace(session, "multilingual-smoke")
                imported = import_records(
                    session, namespace, RECORDS, settings.max_record_bytes, False
                )
                while process_one_job(session, settings):
                    pass
                result = ask(session, namespace, QUESTION, "answer", settings)

            target = next(
                (
                    item
                    for item in result["retrieval"]
                    if item["external_id"] == "hindi-dentist"
                ),
                None,
            )
            passed = bool(
                imported["accepted"] == 2
                and result["evidence"]
                and result["evidence"][0]["external_id"] == "hindi-dentist"
                and target
                and target["lexical_rank"] is None
                and target["dense_rank"] is not None
            )
            report = {
                "created_at": datetime.now(UTC).isoformat(),
                "embedding_model": settings.embedding_model,
                "question_language": "en",
                "target_language": "hi",
                "question": QUESTION,
                "required_external_id": "hindi-dentist",
                "top_external_id": result["evidence"][0]["external_id"]
                if result["evidence"]
                else None,
                "target_retrieval": target,
                "passed": passed,
            }
        finally:
            engine.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
