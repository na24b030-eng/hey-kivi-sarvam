# Transcript Import Specification

Kivi accepts line-delimited JSON (`.jsonl`) files in UTF-8. Each line is a single transcript record with raw ASR output, formatted text, and optional metadata.

---

## Schema

The normative JSON Schema lives in [`backend/schemas/transcript.schema.json`](../backend/schemas/transcript.schema.json).

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

## Fields

| Field | Type | Required | Description |
|:---|:---:|:---:|:---|
| `schema_version` | integer | **Yes** | Must be `1` |
| `id` | string | **Yes** | Unique ID within the target namespace |
| `raw_asr` | string | **Yes** | Raw, unformatted speech recognition output |
| `formatted_text` | string | **Yes** | Cleaned, punctuated text |
| `occurred_at` | string | No | RFC 3339 timestamp (e.g., `2026-09-07T10:30:00+05:30`) |
| `timezone` | string | No | IANA timezone (e.g., `Asia/Kolkata`) |
| `app` | string | No | Source application |
| `language_hints` | string[] | No | ISO language codes (e.g., `["en", "hi"]`) |
| `context` | object | No | Arbitrary metadata dict |

---

## How Ingestion Works

1. **Dual-View Indexing** — Both `raw_asr` and `formatted_text` are chunked and embedded separately. Raw enables phonetic search; formatted produces clean answers.
2. **Semantic Extraction** — If `SARVAM_API_KEY` is configured, entities, aliases, and relationships are extracted from each record and stored in the knowledge graph. Without the key, this step is gracefully skipped.
3. **Atomic Validation** — If any line fails schema validation, the entire import is rejected with specific line-level error diagnostics.
4. **Idempotency** — Re-importing the same `id` with identical content is a no-op. Changing content under an existing `id` triggers a revision update.

---

## CLI Import

```powershell
# Import a file into a namespace
uv run --project backend python -m kivi_memory.cli import --namespace my_workspace --file path/to/transcripts.jsonl

# Process all queued records (chunking, embedding, entity extraction)
uv run --project backend python -m kivi_memory.cli worker --drain

# Verify the import
uv run --project backend python -m kivi_memory.cli inspect --namespace my_workspace
```
