# Deploy the Kivi frontend on Vercel

This deployment publishes the React/Vite frontend from the existing GitHub repository. The committed `vercel.json` supplies the install command, production build and output directory. The application currently has no client-side URL routes, so no catch-all rewrite is needed.

The frontend supports two operating modes:
1. **Live Backend Mode (Active Production State)**: With `VITE_API_BASE_URL=https://hey-kivi-sarvam.onrender.com`, the Vercel frontend communicates directly with the FastAPI backend on Render. This provides SQLite persistence, worker queue execution, embeddings, query tracing, and optional Sarvam generation.
2. **Browser-Local Fallback**: If `VITE_API_BASE_URL` is not provided (e.g. for standalone forks or static review), the UI falls back to storing workspaces and records in browser `localStorage`.

## Connected Render Backend

The production frontend on Vercel is connected to the live backend hosted on Render:
- **Render Backend URL**: `https://hey-kivi-sarvam.onrender.com`
- **Health Check**: `https://hey-kivi-sarvam.onrender.com/api/health`
- **Readiness Probe**: `https://hey-kivi-sarvam.onrender.com/api/readiness`
- **CORS**: Configured on Render via `TRUSTED_ORIGINS` to allow `https://hey-kivi-sarvam-dnaq.vercel.app`.

For instructions on deploying or managing the backend on Render, see [docs/deploy-render.md](deploy-render.md).

## Create the Vercel project

1. In Vercel, choose **Add New → Project** and import `na24b030-eng/hey-kivi-sarvam` from GitHub.
2. Keep the repository root as the Vercel **Root Directory**. The root `vercel.json` already selects Vite and runs `npm --prefix frontend ci` followed by `npm --prefix frontend run build`.
3. Deploy. Vercel publishes `frontend/dist` and the app opens in browser-local demo mode.
4. For the full backend workflow, add `VITE_API_BASE_URL` under **Environment Variables** for Production and Preview. Set it to the backend origin without `/api` or a trailing slash, for example `https://hey-kivi-sarvam.onrender.com`, then redeploy.

Vite exposes `VITE_*` variables in browser code. Put only the backend's public URL in `VITE_API_BASE_URL`; never put `SARVAM_API_KEY` or another secret there.

## Allow the frontend origin on the backend

The backend automatically allows requests from `*.vercel.app` and `https://hey-kivi-sarvam-dnaq.vercel.app` by default. If using a custom domain, configure the backend's `TRUSTED_ORIGINS`:

```text
TRUSTED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000,http://127.0.0.1:5173,https://hey-kivi-sarvam-dnaq.vercel.app
```

Restart the backend after changing this value.

## Verify

1. Open `https://hey-kivi-sarvam-dnaq.vercel.app` and create a memory space.
2. Import a sample JSONL record.
3. Ask a question, open its evidence, and test correction or suppression.
4. If `VITE_API_BASE_URL` is configured, confirm in browser developer tools that API requests route to the configured HTTPS backend (`https://hey-kivi-sarvam.onrender.com`) without CORS errors.

If the application reports that the backend cannot be reached, open `${VITE_API_BASE_URL}/api/readiness` directly to verify that the Render backend is healthy and that `TRUSTED_ORIGINS` includes your Vercel deployment domain.

> **Production Note:** Workspaces are partitioned by namespace. For multi-tenant production environments containing sensitive data, front this service with an authentication gateway (e.g., OAuth, Cloudflare Access, or API Gateway).

