# Local frontend development

Prerequisites: Python 3.14 (existing venv with project dependencies), Node 24+, npm, local archive (or a configured PostgreSQL analytics source), optional LLM provider configuration from `.env.example`. Never put provider or database credentials in `frontend/` or in `VITE_*` variables. This is a **local development server**, not a production-authenticated multi-user API.

From the repository root:

```bash
python -m pip install -r requirements.txt
python -m uvicorn app.api.main:app --reload --host 127.0.0.1 --port 8000
```

In a second shell:

```bash
cd frontend
npm ci
npm run dev
```

Open http://127.0.0.1:5173/conversation/new. Vite proxies `/api` to `127.0.0.1:8000`. For direct cross-origin access the FastAPI CORS allow-list is `http://localhost:5173` by default; set `FRONTEND_DEV_ORIGIN` to your **specific** origin if necessary. Never use `*` with credentials. API docs: http://127.0.0.1:8000/docs. API and Vite should be one worker each until the run log is durable/shared.

Tests: `pytest -q tests/test_frontend_api.py tests/test_frontend_schema_drift.py`; `cd frontend && npm test && npm run build && npx playwright install chromium && npx playwright install-deps chromium && npm run e2e`. Browser E2E runs a **real** Python Engine and uses the configured data/provider; it is not deterministic offline. For schema changes: `cd frontend && npm run types:generate`, update the Zod validators and run the contract tests.

Deployment is deliberately postponed: build static assets with `npm run build`, serve alongside Python API using a same-origin reverse proxy; a separate static origin needs explicit CORS and authentication. Before public use add authentication/authorization, rate limits, CSRF policy, persistent shared event log and secure multi-worker session coordination.
