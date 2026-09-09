# Deploy the Kivi frontend on Vercel

This deployment publishes the React/Vite frontend from the existing GitHub repository. The committed `vercel.json` supplies the install command, production build and output directory. The application currently has no client-side URL routes, so no catch-all rewrite is needed.

## Required backend

Vercel serves this frontend as static files. It does not host this project's persistent FastAPI API, SQLite database, background worker or local E5 model. Before deploying the frontend, run the backend on a host with:

- a stable public HTTPS origin, such as `https://api.example.com`;
- persistent storage for `APP_DATA_DIR`;
- the documented migration and start commands from `RUN.md`;
- `SARVAM_API_KEY` stored only in backend environment variables when generated answers are enabled.

A backend bound to `127.0.0.1` on your computer is unreachable from the deployed site.

## Create the Vercel project

1. In Vercel, choose **Add New → Project** and import `na24b030-eng/hey-kivi-sarvam` from GitHub.
2. Keep the repository root as the Vercel **Root Directory**. The root `vercel.json` already selects Vite and runs `npm --prefix frontend ci` followed by `npm --prefix frontend run build`.
3. Under **Environment Variables**, add `VITE_API_BASE_URL` for Production and Preview. Set it to the backend origin without `/api` or a trailing slash, for example `https://api.example.com`.
4. Deploy. Vercel publishes `frontend/dist`. The build deliberately fails with a clear message if `VITE_API_BASE_URL` is missing or is not an HTTPS origin.

Vite exposes `VITE_*` variables in browser code. Put only the backend's public URL in `VITE_API_BASE_URL`; never put `SARVAM_API_KEY` or another secret there.

## Allow the frontend origin on the backend

After Vercel assigns the production URL, configure the backend with its exact origin:

```text
TRUSTED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000,http://127.0.0.1:5173,https://your-project.vercel.app
```

Restart the backend after changing this value. Add any preview URL you actively test as another comma-separated origin. Avoid a broad wildcard for this data-bearing API.

## Verify

1. Open `https://your-project.vercel.app` and create a memory space.
2. Import a fictional JSONL record and wait for processing.
3. Ask a question, open its evidence, and test correction or suppression.
4. In browser developer tools, confirm API requests go to the configured HTTPS backend and do not have CORS or mixed-content errors.

If the page reports that the server returned non-JSON content, the frontend is reaching a web page or platform 404 instead of FastAPI. Confirm that `VITE_API_BASE_URL` exists in the Vercel environment used by the deployment, contains only the backend origin, and that you redeployed after saving it. If the page reports that it cannot reach the backend, open `${VITE_API_BASE_URL}/api/readiness` directly and verify that the backend includes the Vercel site in `TRUSTED_ORIGINS`.

The current build has no user authentication. Use fictional or sanitized records on any public deployment. Add authentication and per-user authorization before exposing personal history.
