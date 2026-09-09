# Transcript import format — v1 contract

Status: implemented by the backend (both locally and on the live Render service). Use the **Import** screen in the web/desktop UI or the CLI documented in [RUN.md](../RUN.md). The contract below is the canonical input format.

Provide UTF-8 JSONL: one JSON object per line. Each object must contain `schema_version: 1`, a nonblank string `id`, and the string fields `raw_asr` and `formatted_text`. The normative shape is in `backend/schemas/transcript.schema.json`. A schema validator must enable date-time format checking; shape validation alone does not validate timestamps.

```json
{"schema_version":1,"id":"example-001","raw_asr":"lantern launch monday not friday","formatted_text":"Lantern launches Monday, not Friday.","occurred_at":"2026-09-07T10:30:00+05:30","timezone":"Asia/Kolkata","app":"Notepad","language_hints":["en"],"context":{"mode":"dictation"}}
```

The two text views remain distinct evidence. Both fields are required, and at least one must contain a non-whitespace character. One view may be empty when the other contains the available transcript; the importer retains that state rather than inventing missing text. Missing fields or a fully blank pair are validation errors.

`occurred_at`, `timezone`, `app`, `language_hints`, and `context` are optional. Missing occurrence time stays unknown. Import time is stored separately. If occurrence time is present, use RFC 3339 with a timezone offset; naive timestamps are rejected. The optional timezone name is retained as source metadata and is not treated as a substitute for an offset.

Unknown top-level metadata is preserved under the source context's `extra` key as inert metadata; it cannot set internal namespace IDs, job states, memory authority or ownership. Language hints are preserved in the same context payload. The importer selects internal fields through explicit application controls. A selected app does not prove a recipient or that the dictated text was sent.

## Import behavior

- Preview validation failures with line numbers before processing. An import containing any invalid row is rejected atomically; valid rows are not silently imported around it.
- Scope external IDs to the selected corpus. Same ID and same stored payload is idempotent. Same ID with different text or metadata is a conflict requiring an explicit replacement revision.
- Keep different IDs as different episodes even when text is repeated. Retrieval may group duplicates; counting must respect the requested unit.
- Preserve Unicode and exact original text for source spans. Normalize only derived search representations.
- Publish file/record size limits with the implementation. Over-limit records produce visible errors; they are never silently truncated.
- Track accepted, source-indexed, memory-processed, blank and failed records separately. Upload completion is not processing completion.
- Keep gold evaluation labels outside the import corpus and production search indexes.

## Mapping another dataset

Map its stable record ID to `id`, raw transcript to `raw_asr`, and formatted transcript to `formatted_text`; add `schema_version: 1`. Preserve ordinary available timestamps and app context. Omit unavailable optional fields. Put extra capture metadata under `context` or preserve it as additional fields. Do not require hand-labeled entities, topics, preferences or answers.

Use `uv run --project backend python -m kivi_memory.cli import --namespace sample --file <corpus.jsonl>`, then start the worker documented in [RUN.md](../RUN.md). These steps accept any compatible corpus without modifying application code.
