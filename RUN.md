# Local setup and operations

Run the commands from the repository root in two PowerShell terminals. This setup exercises migrations, the generated 500-record corpus, queue processing, evidence evaluation, and the built desktop UI.

Tested runtime: **Python 3.12.13**, **uv 0.11.3**, **Node.js 24.15.0**, and **npm 11.12.1** on Windows. Python package versions are locked in `backend/uv.lock`; frontend package versions are locked in `frontend/package-lock.json`.

## Environment variables

No environment variable is required for an offline local run. Copy `backend/.env.example` to `backend/.env` only to configure documented overrides. `SARVAM_API_KEY` is optional and enables generated answers; never place it in frontend code or commit the real `.env`. `APP_DATA_DIR` selects an isolated data directory, and `EMBEDDING_CACHE_DIR` can point to a previously downloaded local model cache.

## Install, initialize, and seed

For an isolated rehearsal, set `APP_DATA_DIR` to an empty writable folder before migration. The command uses uv's copy mode because this repository may live in OneDrive, where package hard links can fail.

```powershell
uv sync --project backend --extra dev --extra embeddings --locked --link-mode copy
npm --prefix frontend ci
npm --prefix frontend run build
uv run --project backend python data/generate_synthetic.py
uv run --project backend python eval/generate_cases.py
uv run --project backend python -m kivi_memory.cli migrate
uv run --project backend python -m kivi_memory.cli doctor --download-embedding
uv run --project backend python -m kivi_memory.cli seed --namespace demo
uv run --project backend python -m kivi_memory.cli worker --drain

# Optional code-quality checks (run from the repository root)
uv run --project backend pytest backend/tests
uv run --project backend ruff check backend/src backend/tests
```

After seeding and processing the demo namespace, run the reproducible 120-case status suite:

```powershell
uv run --project backend python -m kivi_memory.cli evaluate --namespace demo --suite eval/cases.jsonl --output eval/results/demo-report.json
```

Verify that the persisted E5 path can retrieve Hindi evidence from an English query without lexical overlap:

```powershell
uv run --project backend python eval/multilingual_embedding_smoke.py
```

With `SARVAM_API_KEY` configured, this small one-per-family smoke suite validates the live provider path and aggregates the returned token usage. It deliberately uses only fictional corpus records:

```powershell
uv run --project backend python -m kivi_memory.cli evaluate --namespace demo --suite eval/live-smoke-cases.jsonl --output eval/results/live-smoke-report.json
```

The corpus-generation command is deterministic and creates exactly 500 fictional records. `doctor --download-embedding` downloads the optional local multilingual E5 model once; it is required to exercise the persisted vector path. If it is unavailable, the app remains runnable with an explicitly deterministic fallback. The UI does not require a Sarvam key for source-backed replay. If `SARVAM_API_KEY` is configured in `backend/.env`, normal answers additionally use the server-side Sarvam adapter and retain the selected source evidence, returned model, latency, and provider usage in the query trace. The default integration sends `reasoning_effort: null` so the bounded answer budget is reserved for the answer rather than hidden reasoning tokens.

`--drain` processes every queued job and exits. To process new imports continuously in a second terminal:

```powershell
uv run --project backend python -m kivi_memory.cli worker
```

Start the application in the first terminal:

```powershell
uv run --project backend python -m kivi_memory.cli serve --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. Create a memory space, paste JSONL in **Import**, then use **Hey Kivi** to ask a question. The answer provides source evidence. **History** has a source inspector for raw/formatted text, passages and derived memories. **Memory** supports correction and suppression. The Import UI validates, imports and drains its local queue in one action; use the CLI worker for a large or continuous import.

For a quick functional check, ask when a project launches, ask an unrelated question to observe abstention, open an answer source, correct or suppress its promoted memory, repeat the question, request a draft, and delete a source from History.

To import an unfamiliar compatible corpus without changing code:

```powershell
uv run --project backend python -m kivi_memory.cli import --namespace sample --file C:\path\to\corpus.jsonl
uv run --project backend python -m kivi_memory.cli worker
uv run --project backend python -m kivi_memory.cli inspect --namespace sample
```

## Inspect state and traces

The memory database defaults to `%LOCALAPPDATA%\KiviMemoryWorkbench\memory.sqlite3`; with `APP_DATA_DIR=C:\path\to\data`, it is `C:\path\to\data\memory.sqlite3`. Use the documented `inspect` command for namespace counts, the source inspector in the UI for provenance, and `GET /api/namespaces/{namespace_id}/query-runs/{trace_id}` for a stored query trace. Generated reports are written under `eval/results/` unless `--output` specifies another path.

Do not put a Sarvam key in the frontend; use the ignored `backend/.env` only when enabling the server-side adapter.

For a hosted static frontend, use the committed Vercel configuration. Without `VITE_API_BASE_URL`, the deployed site runs browser-local demo mode with `localStorage`. To connect the full FastAPI/SQLite/worker/Sarvam path, set `VITE_API_BASE_URL` to a separately hosted HTTPS backend and add the deployed frontend's exact origin to that backend's `TRUSTED_ORIGINS`; see [docs/deploy-vercel.md](docs/deploy-vercel.md).

Reset only the named namespace after stopping the API and worker:

```powershell
uv run --project backend python -m kivi_memory.cli reset --namespace demo
```

This deletes that namespace's imported sources and their cascaded chunks/memories/jobs, plus its query traces and lifecycle-operation metadata. It keeps the empty namespace so the same workspace can be reused. It never touches the installed Kivi application, its settings, or any other namespace.
