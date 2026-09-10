# Operations & Runbook

Everything you need to run, evaluate, and manage Kivi locally or in the cloud.

---

## 1. System Requirements

| Requirement | Version | Notes |
|:---|:---|:---|
| Python | 3.12+ | Managed via [uv](https://docs.astral.sh/uv/) |
| Node.js | 20+ | With npm |
| OS | Windows, macOS, Linux | Tested on all three |

Locked dependency versions: `backend/uv.lock` and `frontend/package-lock.json`.

---

## 2. Environment Variables

No env vars are required for local/offline operation. The engine defaults to local vector embeddings and direct source replay.

To customize, copy `backend/.env.example` to `backend/.env`:

| Variable | Required | Default | What it does |
|:---|:---:|:---|:---|
| `SARVAM_API_KEY` | No | `None` | Enables LLM synthesis via Sarvam 105B + semantic entity extraction. Strictly server-side. |
| `APP_DATA_DIR` | No | OS AppData | Where SQLite database and app files live |
| `EMBEDDING_CACHE_DIR` | No | System cache | Local cache for `intfloat/multilingual-e5-small` model |
| `HOST` / `PORT` | No | `127.0.0.1` / `8000` | Server binding |
| `TRUSTED_ORIGINS` | No | Localhost origins | CORS allowed origins (comma-separated) |

### Pillar 4 Settings (Semantic Intelligence)

These are configured in `backend/src/kivi_memory/settings.py` with sensible defaults:

| Setting | Default | What it controls |
|:---|:---|:---|
| `enable_semantic_extraction` | `True` | Toggle LLM-powered entity/relation extraction during ingestion |
| `multi_hop_max_depth` | `2` | Maximum traversal depth for multi-hop bridge expansion |
| `multi_hop_bridge_limit` | `4` | Max bridge entities to follow per hop |
| `decay_half_life_days` | `1.0` | Half-life for exponential decay on scheduled/ephemeral memories |
| `contradiction_time_window_hours` | `1.0` | Time window for detecting contradicting claims |

---

## 3. Installation

```powershell
# Install Python backend dependencies (locked versions)
uv sync --project backend --extra dev --extra embeddings --locked --link-mode copy

# Install frontend dependencies and build static assets
npm --prefix frontend ci
npm --prefix frontend run build
```

> **Windows/OneDrive note:** Use `--link-mode copy` to avoid hard-link failures on OneDrive-synced directories.

---

## 4. Database Setup

The SQLite schema is managed through 5 Alembic migrations:

```powershell
# Run all migrations (0001 → 0005, including the knowledge graph tables)
uv run --project backend python -m kivi_memory.cli migrate

# Download the multilingual embedding model (runs once, ~450MB)
uv run --project backend python -m kivi_memory.cli doctor --download-embedding

# Generate seed data and populate the demo workspace
uv run --project backend python data/generate_synthetic.py
uv run --project backend python eval/generate_cases.py
uv run --project backend python -m kivi_memory.cli seed --namespace demo

# Process all queued ingestion jobs (includes semantic extraction)
uv run --project backend python -m kivi_memory.cli worker --drain
```

### What the migrations create

| Migration | Tables |
|:---|:---|
| 0001 | `namespaces`, `sources`, `source_chunks`, `embeddings`, `memories` |
| 0002 | `ingestion_jobs` (background queue) |
| 0003 | `query_runs`, `query_sources` (audit trail) |
| 0004 | Schema refinements |
| 0005 | `entities`, `entity_aliases`, `entity_mentions`, `entity_relations` + `decay_class`/`expires_at` on memories |

---

## 5. Running the Application

### Terminal 1: Application Server
```powershell
uv run --project backend python -m kivi_memory.cli serve --host 127.0.0.1 --port 8000
```

### Terminal 2: Continuous Background Worker
```powershell
uv run --project backend python -m kivi_memory.cli worker
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser.

---

## 6. Key Workflows

### Context Recovery
In **Hey Kivi**, ask: *"When does Harbor launch after Dev approves the release checklist?"*
→ Returns the exact date with source citation chips linking back to the original transcript.

### Multi-Hop Reasoning
Ask: *"Who manages the person working on Project Lantern?"*
→ Kivi bridges across separate conversations: one where someone was assigned to Lantern, another where their manager was mentioned.

### Abstention on Missing History
Ask something unrecorded: *"What is the capital of Peru?"*
→ Explicit refusal to invent facts (`insufficient_evidence`). No hallucination.

### Contradiction Detection
If two recent notes conflict (e.g., "meeting is Wednesday" vs "meeting moved to Thursday"), Kivi surfaces an amber **disambiguation card** in the UI asking you to clarify.

### Memory Governance
In **Memory**, edit an active claim or suppress a source. Re-ask the question; the updated memory state is reflected immediately.

### Source Inspector
In **History**, select any transcript to see side-by-side: raw ASR, formatted text, chunk boundaries, derived memory candidates, and extracted entities.

---

## 7. Evaluation & Benchmarks

Run the reproducible benchmark suites:

```powershell
# 1. 120-Case Grounded Retrieval & Abstention Benchmark (Offline)
uv run --project backend python -m kivi_memory.cli evaluate --namespace demo --suite eval/cases.jsonl --output eval/results/demo-report.json --offline

# 2. Cross-Lingual Zero-Overlap Embedding Test
uv run --project backend python eval/multilingual_embedding_smoke.py

# 3. Live Provider Smoke Suite (requires SARVAM_API_KEY)
uv run --project backend python -m kivi_memory.cli evaluate --namespace demo --suite eval/live-smoke-cases.jsonl --output eval/results/live-smoke-report.json
```

### Test suites

```powershell
# Backend tests (55 tests including 15 semantic intelligence tests)
uv run --project backend pytest backend/tests

# Backend linting
uv run --project backend ruff check backend/src backend/tests eval/multilingual_embedding_smoke.py

# Frontend tests
npm --prefix frontend test

# Frontend build
npm --prefix frontend run build
```

All of these run automatically on every push via [GitHub Actions CI](.github/workflows/ci.yml).

---

## 8. Importing External Datasets

Ingest any compatible JSONL transcript file:

```powershell
# Import records into a namespace
uv run --project backend python -m kivi_memory.cli import --namespace my_workspace --file path/to/transcripts.jsonl

# Process all queued records (chunking, embedding, semantic extraction)
uv run --project backend python -m kivi_memory.cli worker --drain

# Check workspace stats
uv run --project backend python -m kivi_memory.cli inspect --namespace my_workspace
```

See [docs/import-format.md](docs/import-format.md) for the JSONL schema.

---

## 9. Inspection & Debugging

```powershell
# Workspace summary (sources, chunks, memories, entities)
uv run --project backend python -m kivi_memory.cli inspect --namespace demo
```

- **Evaluation logs**: Detailed latency, token, and score reports in [`eval/results/`](eval/results/)
- **Query audit traces**: Per-query provenance via API: `GET /api/namespaces/{ns}/query-runs/{trace_id}`

---

## 10. Workspace Reset

Wipe a namespace while preserving schema integrity:

```powershell
uv run --project backend python -m kivi_memory.cli reset --namespace demo
```

This cascades across sources, chunks, embeddings, memories, entities, relations, and query traces within that namespace.

---

## 11. Cloud Deployment

The project is live on free tiers:

| Platform | URL | Docs |
|:---|:---|:---|
| **Frontend (Vercel)** | [hey-kivi-sarvam-dnaq.vercel.app](https://hey-kivi-sarvam-dnaq.vercel.app) | [docs/deploy-vercel.md](docs/deploy-vercel.md) |
| **Backend (Render)** | [hey-kivi-sarvam.onrender.com](https://hey-kivi-sarvam.onrender.com) | [docs/deploy-render.md](docs/deploy-render.md) |
| **CI** | [GitHub Actions](https://github.com/na24b030-eng/hey-kivi-sarvam/actions) | [.github/workflows/ci.yml](.github/workflows/ci.yml) |

Both auto-deploy from the `master` branch on push.
