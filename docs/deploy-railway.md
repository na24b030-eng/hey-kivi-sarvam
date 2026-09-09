# Deploy Kivi Memory Workbench on Railway

This deployment runs the existing React client and FastAPI API as one service. It uses Railway's native **Railpack** builder, so no Dockerfile or Docker Desktop is needed. The browser makes same-origin API requests, and the server keeps the Sarvam key private.

This assignment build has no user authentication. Treat a public Railway domain as a short-lived demonstration and import only fictional or sanitized records. Add authentication and per-user authorization before using it with personal history.

## Create the service

1. In Railway, create a new project and select the GitHub repository `na24b030-eng/hey-kivi-sarvam`.
2. In **Settings → Build**, use **Railpack**, leave the root directory as the repository root, and set the custom build command:

   ```text
   npm --prefix frontend ci && npm --prefix frontend run build && python -m pip install "./backend[embeddings]"
   ```

3. In **Settings → Deploy**, set the custom start command:

   ```text
   cd backend && python -m kivi_memory.cli migrate && exec uvicorn kivi_memory.api:create_app --factory --host 0.0.0.0 --port $PORT
   ```

   Set the healthcheck path to `/api/readiness`.

4. In **Variables**, add the following values:

   ```text
   APP_DATA_DIR=/data
   FRONTEND_DIST_DIR=/app/frontend/dist
   SARVAM_API_KEY=your_Sarvam_subscription_key
   ```

   `SARVAM_API_KEY` is optional for source-backed replay, but needed for Sarvam-generated answers and drafts. Do not add it to GitHub or a frontend variable.

5. In the service's **Volumes** settings, add a volume and mount it at `/data`.
6. In **Networking**, generate a public Railway domain.
7. Deploy. The start command runs Alembic migrations and then starts Uvicorn on Railway's `PORT`.

## Verify

Open these URLs after the deployment reports healthy:

```text
https://YOUR-DOMAIN/api/health
https://YOUR-DOMAIN/api/readiness
https://YOUR-DOMAIN/
```

`/api/readiness` should report `database: "ready"`. It reports whether a Sarvam key is configured without revealing it.

## Operating notes

- Keep a single running replica while using SQLite: one Railway volume belongs to one service instance, and Kivi stores SQLite, WAL files, and embedding cache together under `/data`.
- Railway volumes are attached only at runtime. The start command therefore applies migrations immediately before starting the API.
- A volume preserves memory through redeploys. Use Kivi's namespace reset command only when you intend to delete a namespace; do not wipe the Railway volume for ordinary redeploys.
- For a production system with horizontal scaling, move persistence from SQLite to managed Postgres and store embeddings in a shared vector-capable service. That is outside this assignment's local-first architecture.
