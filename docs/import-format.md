# Transcript Import Specification

Kivi Memory accepts line-delimited JSON (`.jsonl`) files encoded in UTF-8. Each line represents an individual transcript record containing raw automatic speech recognition output, formatted text, and optional contextual metadata.

---

## Schema Overview

The normative JSON Schema is maintained in [`backend/schemas/transcript.schema.json`](../backend/schemas/transcript.schema.json).

### Example Record

```json
{
  "schema_version": 1,
  "id": "dictation-2026-09-07-001",
  "raw_asr": "lantern launch monday not friday",
  "formatted_text": "Lantern launches Monday, not Friday.",
  "occurred_at": "2026-09-07T10:30:00+05:30",
  "timezone": "Asia/Kolkata",
  "app": "Notepad",
  "language_hints": ["en"],
  "context": {
    "mode": "dictation"
  }
}
```

---

## Field Reference

| Field | Type | Required | Description |
| :--- | :---: | :---: | :--- |
| `schema_version` | integer | **Yes** | Schema version identifier (must be `1`). |
| `id` | string | **Yes** | Unique identifier for the transcript within the target namespace. |
| `raw_asr` | string | **Yes** | Raw, unformatted speech recognition output. |
| `formatted_text` | string | **Yes** | Cleaned, punctuated, and formatted transcript text. |
| `occurred_at` | string | No | RFC 3339 timestamp with timezone offset (e.g., `2026-09-07T10:30:00+05:30`). |
| `timezone` | string | No | IANA timezone identifier (e.g., `Asia/Kolkata`). |
| `app` | string | No | Source application where dictation occurred. |
| `language_hints` | array of strings | No | ISO language codes (e.g., `["en", "hi"]`). |
| `context` | object | No | Arbitrary dictionary containing custom application or interaction metadata. |

---

## Ingestion Semantics

1. **Text Complementarity**: While both `raw_asr` and `formatted_text` are required fields, one may be empty if only a single text representation is available. Records where both fields are completely blank or whitespace are rejected.
2. **Dual-View Indexing**: Both raw and formatted representations are chunked and embedded separately, enabling phonetic search without compromising formatted response generation.
3. **Atomic File Validation**: Batch imports are validated prior to ingestion. If any line violates schema validation or timestamp formats, the entire import is rejected with specific line error diagnostics.
4. **Idempotency**: Submitting an identical record payload under an existing `id` is a safe, no-op idempotent action. Modifying a record's contents under an existing ID initiates a revision update.

---

## CLI Ingestion

To ingest a transcript file via the command line:

```powershell
uv run --project backend python -m kivi_memory.cli import --namespace <workspace_name> --file <path_to_transcripts.jsonl>
uv run --project backend python -m kivi_memory.cli worker --drain
```

