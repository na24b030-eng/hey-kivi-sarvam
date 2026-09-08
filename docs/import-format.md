# Transcript import format — v1 contract

Status: implemented by the local backend. The reviewer can use the normal desktop **Import** screen or the CLI documented in [RUN.md](../RUN.md). The contract below remains normative.

Provide UTF-8 JSONL: one JSON object per line. Each object must contain `schema_version: 1`, a nonblank string `id`, and the string fields `raw_asr` and `formatted_text`. The normative shape is in `backend/schemas/transcript.schema.json`. A schema validator must enable date-time format checking; shape validation alone does not validate timestamps.

```json
{"schema_version":1,"id":"example-001","raw_asr":"lantern launch monday not friday","formatted_text":"Lantern launches Monday, not Friday.","occurred_at":"2026-09-07T10:30:00+05:30","timezone":"Asia/Kolkata","app":"Notepad","language_hints":["en"],"context":{"mode":"dictation"}}
```

The two text views remain distinct evidence. Both fields are required, but an empty string is representable: the importer will retain it and report the source as empty or one-view-only rather than invent a missing transcription. Records with both views blank are retained for inspection and excluded from semantic processing. This edge-case policy refines the initial blueprint's strict missing-view rule: absent fields are validation errors; explicitly empty strings are observable capture data.

`occurred_at`, `timezone`, `app`, `language_hints`, and `context` are optional. Missing occurrence time stays unknown. Import time is stored separately. If occurrence time is present, use RFC 3339 with a timezone offset; do not guess offsets when converting another corpus. The optional timezone name will be validated separately against supported timezones, with conflicts reported explicitly.

Unknown metadata is preserved as inert metadata; it cannot set internal namespace IDs, job states, memory authority or ownership. The importer selects these through explicit application controls. A selected app does not prove a recipient or that the dictated text was sent.

## Planned import behavior

- Preview validation failures with line numbers before processing. Offer explicit valid-row-only import; do not silently skip invalid records.
- Scope external IDs to the selected corpus. Same ID and same stored payload is idempotent. Same ID with different text or metadata is a conflict requiring an explicit replacement revision.
- Keep different IDs as different episodes even when text is repeated. Retrieval may group duplicates; counting must respect the requested unit.
- Preserve Unicode and exact original text for source spans. Normalize only derived search representations.
- Publish file/record size limits with the implementation. Over-limit records produce visible errors; they are never silently truncated.
- Track accepted, source-indexed, memory-processed, blank and failed records separately. Upload completion is not processing completion.
- Keep gold evaluation labels outside the import corpus and production search indexes.

## Mapping another dataset

Map its stable record ID to `id`, raw transcript to `raw_asr`, and formatted transcript to `formatted_text`; add `schema_version: 1`. Preserve ordinary available timestamps and app context. Omit unavailable optional fields. Put extra capture metadata under `context` or preserve it as additional fields. Do not require hand-labeled entities, topics, preferences or answers.

Use `uv run --project backend python -m kivi_memory.cli import --namespace reviewer --file <corpus.jsonl>`, then start the worker documented in [RUN.md](../RUN.md). These steps accept an unfamiliar compatible corpus without modifying application code.
