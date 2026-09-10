# Deploying the Kivi Frontend on Vercel

The React/Vite frontend deploys from the existing GitHub repo. The committed `vercel.json` handles the install command, build, and output directory.

## Operating Modes

The frontend supports two modes:

1. **Live Backend Mode** (production) — With `VITE_API_BASE_URL` pointing to the Render backend, you get full functionality: SQLite persistence, background worker processing, semantic extraction, multi-hop reasoning, contradiction disambiguation cards, and Sarvam 105B generation.

2. **Browser-Local Fallback** — Without `VITE_API_BASE_URL`, the UI stores workspaces in browser `localStorage`. Good for quick demos or standalone forks.

## Current Live Setup

| Component | URL |
|:---|:---|
| Frontend | [hey-kivi-sarvam-dnaq.vercel.app](https://hey-kivi-sarvam-dnaq.vercel.app) |
| Backend | [hey-kivi-sarvam.onrender.com](https://hey-kivi-sarvam.onrender.com) |
| Health | [/api/health](https://hey-kivi-sarvam.onrender.com/api/health) |

CORS is configured on Render via `TRUSTED_ORIGINS` to allow the Vercel domain.

---

## Deploying

1. In Vercel → **Add New → Project** → import `na24b030-eng/hey-kivi-sarvam` from GitHub
2. Keep the repo root as the Vercel **Root Directory** — `vercel.json` handles everything:
   - Install: `npm --prefix frontend ci`
   - Build: `npm --prefix frontend run build`
   - Output: `frontend/dist`
3. Deploy. The app opens in browser-local fallback mode.
4. For full backend features, add `VITE_API_BASE_URL` under **Environment Variables**:
   - Value: `https://hey-kivi-sarvam.onrender.com` (no `/api`, no trailing slash)
   - Target: Production, Preview, Development
5. Redeploy.

> **Security:** `VITE_*` variables are embedded in browser code. Only put the backend's public URL here — never put `SARVAM_API_KEY` or other secrets.

---

## CORS Configuration

The backend auto-allows `*.vercel.app` and `https://hey-kivi-sarvam-dnaq.vercel.app` by default. For custom domains, update the backend's `TRUSTED_ORIGINS`:

```text
TRUSTED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000,https://hey-kivi-sarvam-dnaq.vercel.app,https://your-custom-domain.com
```

Restart the backend after changing this.

---

## Verify

1. Open your Vercel app URL
2. Create a memory space and import a JSONL record
3. Ask a question → check for source citations
4. Test contradiction detection: import two conflicting records and query — you should see an amber disambiguation card
5. In browser dev tools, confirm API requests route to the Render backend without CORS errors

For backend deployment docs, see [deploy-render.md](deploy-render.md).
