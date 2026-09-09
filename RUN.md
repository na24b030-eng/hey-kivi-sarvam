# Operations & Runbook

**Primary Review Method:** Local Application Review (FastAPI + React Desktop UI with SQLite persistence).

This guide documents environment setup, automated migrations, queue processing, UI workflows, benchmark evaluation, and administrative commands.

---

## 1. System Requirements & Runtime Versions

The system is tested and supported on Windows, macOS, and Linux:

- **Python**: `3.12.13` (managed via **uv `0.11.3`**)
- **Node.js**: `24.15.0` (with **npm `11.12.1`**)
- **Dependencies**: Locked in `backend/uv.lock` and `frontend/package-lock.json`.

---

## 2. Configuration & Environment Variables

No environment variables are required for standard local/offline operation. The engine defaults to deterministic local vector embeddings and direct source replay.

To customize settings, copy `backend/.env.example` to `backend/.env`:

| Variable | Required | Default | Description |
| :--- | :---: | :--- | :--- |
| `SARVAM_API_KEY` | No | `None` | Enables server-side LLM answer synthesis using `sarvam-105b`. Key is strictly server-side and never exposed to the client. |
| `APP_DATA_DIR` | No | OS AppData / Local | Directory for the SQLite database and application files. |
| `EMBEDDING_CACHE_DIR` | No | System cache | Local cache directory for `intfloat/multilingual-e5-small`. |
| `HOST` / `PORT` | No | `127.0.0.1` / `8000` | Host binding and port for the FastAPI server. |
| `TRUSTED_ORIGINS` | No | Localhost origins | Comma-separated list of allowed CORS origins for web clients. |

---

## 3. Installation & Dependency Setup

Run these commands from the repository root:

```powershell
# Install Python backend dependencies with locked versions
uv sync --project backend --extra dev --extra embeddings --locked --link-mode copy

# Install frontend dependencies and compile static assets
npm --prefix frontend ci
npm --prefix frontend run build
```

---

## 4. Database Migration & Seeding

Initialize the SQLite database, download the embedding model, and populate the seed dataset:

```powershell
# Run Alembic schema migrations (0001 -> 0004)
uv run --project backend python -m kivi_memory.cli migrate

# Cache the local multilingual vector model (runs once)
uv run --project backend python -m kivi_memory.cli doctor --download-embedding

# Generate seed datasets and initialize the demo workspace
uv run --project backend python data/generate_synthetic.py
uv run --project backend python eval/generate_cases.py
uv run --project backend python -m kivi_memory.cli seed --namespace demo

# Process all queued ingestion jobs
uv run --project backend python -m kivi_memory.cli worker --drain
```

---

## 5. Starting the Application

The system uses a web server and a background queue worker. Run these commands in separate terminals:

### Terminal 1: Application Server
```powershell
uv run --project backend python -m kivi_memory.cli serve --host 127.0.0.1 --port 8000
```

### Terminal 2: Continuous Background Worker
```powershell
uv run --project backend python -m kivi_memory.cli worker
```

---

## 6. Accessing the Interface

Open your browser to:
**[http://127.0.0.1:8000](http://127.0.0.1:8000)**

The desktop web interface will load with direct connectivity to the local FastAPI backend.

---

## 7. Key Workflows & Primary Interactions

1. **Context Recovery**:
   - In **Hey Kivi**, ask: *"When does Harbor launch after Dev approves the release checklist?"*
   - Observe the returned answer with exact date and source citation chips.
2. **Abstention on Missing History**:
   - Ask an unrecorded or out-of-domain question: *"What is the capital of Peru?"*
   - Observe the system's explicit refusal to invent facts (`insufficient_evidence`).
3. **Source Inspector**:
   - In **History**, select any transcript to inspect side-by-side: Raw ASR, Formatted text, Chunk boundaries, and Derived memory candidates.
4. **Memory Governance (Correction & Suppression)**:
   - In **Memory**, edit an active claim or suppress a source.
   - Re-ask the question in **Hey Kivi** to observe the updated memory state instantly reflected in subsequent answers.
5. **Grounded Drafting**:
   - Request a response draft: *"Draft a status update for the Harbor project."*
   - The generated draft cites underlying interaction records for every claim.

---

## 8. Automated Evaluation & Benchmarks

Run the reproducible benchmark suites from the repository root:

```powershell
# 1. 120-Case Grounded Retrieval & Abstention Benchmark (Offline)
uv run --project backend python -m kivi_memory.cli evaluate --namespace demo --suite eval/cases.jsonl --output eval/results/demo-report.json --offline

# 2. Cross-Lingual Zero-Overlap Embedding Test
uv run --project backend python eval/multilingual_embedding_smoke.py

# 3. Live Provider Smoke Suite (Requires SARVAM_API_KEY)
uv run --project backend python -m kivi_memory.cli evaluate --namespace demo --suite eval/live-smoke-cases.jsonl --output eval/results/live-smoke-report.json
```

---

## 9. Importing External Datasets

To ingest any compatible JSONL transcript dataset without altering code:

```powershell
# Import records into a target namespace
uv run --project backend python -m kivi_memory.cli import --namespace test_workspace --file C:\path\to\transcripts.jsonl

# Process all queued records
uv run --project backend python -m kivi_memory.cli worker --drain

# Inspect workspace statistics
uv run --project backend python -m kivi_memory.cli inspect --namespace test_workspace
```

*(See [docs/import-format.md](docs/import-format.md) for data schema details.)*

---

## 10. State & Provenance Inspection

- **CLI Workspace Summary**:
  ```powershell
  uv run --project backend python -m kivi_memory.cli inspect --namespace demo
  ```
- **Evaluation Logs**: Detailed latency, token, and score reports are stored in [`eval/results/`](eval/results/).
- **Query Audit Traces**: Stored per query run and retrievable via API:
  `GET /api/namespaces/{namespace_id}/query-runs/{trace_id}`

---

## 11. Workspace Reset

To safely wipe a specific namespace's records and memory graph while preserving schema integrity:

```powershell
uv run --project backend python -m kivi_memory.cli reset --namespace demo
```

This cascades deletions across sources, chunks, embeddings, extracted memories, and query traces within that namespace without affecting other data.

---

## Cloud Deployment (Hosted Alternative)

For cloud execution, the repository includes ready-to-deploy configurations:
- **Frontend**: Hosted on [Vercel](https://hey-kivi-sarvam-dnaq.vercel.app/) (see [docs/deploy-vercel.md](docs/deploy-vercel.md))
- **Backend**: Hosted on [Render](https://hey-kivi-sarvam.onrender.com/) (see [docs/deploy-render.md](docs/deploy-render.md))

