# Deploy Kivi Memory Workbench on Railway

This deployment runs the existing React client and FastAPI API as one service. The browser makes same-origin API requests, and the server keeps the Sarvam key private.

## Create the service

1. In Railway, create a new project and select the GitHub repository `na24b030-eng/hey-kivi-sarvam`.
2. Railway detects the root `Dockerfile`. Leave the service root directory at the repository root.
3. In **Variables**, add the following values:

   ```text
   APP_DATA_DIR=/data
   SARVAM_API_KEY=your_Sarvam_subscription_key
   ```

   `SARVAM_API_KEY` is optional for source-backed replay, but needed for Sarvam-generated answers and drafts. Do not add it to GitHub or a frontend variable.

4. In the service's **Volumes** settings, add a volume and mount it at `/data`.
5. In **Networking**, generate a public Railway domain.
6. Deploy. The image command runs Alembic migrations and then starts Uvicorn on Railway's `PORT`.

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
- Railway volumes are attached only at runtime. The Docker command therefore applies migrations immediately before starting the API.
- A volume preserves memory through redeploys. Use Kivi's namespace reset command only when you intend to delete a namespace; do not wipe the Railway volume for ordinary redeploys.
- For a production system with horizontal scaling, move persistence from SQLite to managed Postgres and store embeddings in a shared vector-capable service. That is outside this assignment's local-first architecture.
