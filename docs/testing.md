# Testing

## Strategy

| Layer | Tool | Runs against |
|---|---|---|
| Backend unit + integration | pytest, pytest-asyncio, httpx | A **real PostgreSQL** database (no SQLite stand-in) |
| Migrations | pytest + Alembic CLI | Real PostgreSQL: upgrade → downgrade → upgrade |
| Frontend components | Vitest + Testing Library (jsdom) | Mocked `fetch` responses |
| Full stack | Docker Compose in CI | db + backend + frontend containers |

Later phases add: tenant-isolation tests (Phase 2), ingestion and reliability tests (Phase 3+), Playwright end-to-end tests and Locust load tests (Phase 10).

## Commands

```bash
# Backend (needs PostgreSQL; `docker compose up -d db` gives you one)
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
export NEXUS_TEST_DATABASE_URL=postgresql+asyncpg://nexus:nexus@localhost:5432/nexusai_test
pytest -v
ruff check . && ruff format --check .

# Frontend
cd frontend
npm ci
npm run typecheck
npm test
npm run build
```

## Phase 1 results (actual runs)

Run on 24 Nov 2026, Linux, Python 3.13, Node 22, PostgreSQL 16.

**Backend: 17 passed**

| File | What it proves |
|---|---|
| `test_health.py` (5) | Liveness is OK; readiness is OK with the real DB and reports latency; readiness returns **503** when the DB is unreachable and **does not leak** the password or host; liveness stays OK when the DB is down; system info endpoint |
| `test_middleware.py` (6) | Request IDs are generated, safe incoming IDs are echoed, unsafe IDs are replaced; unhandled errors return a 500 with the request ID and no internal details; CORS allows the configured origin and rejects others |
| `test_config.py` (4) | Non-async database drivers are rejected at startup; comma-separated CORS origins parse; API docs are disabled in production |
| `test_migrations.py` (2) | Migrations upgrade to head, downgrade to base and re-apply cleanly; there is exactly one migration head |

Lint: `ruff check` and `ruff format --check` clean.

**Frontend: 4 passed**, plus `tsc` type check and production build OK.

| Test | What it proves |
|---|---|
| Healthy status | Dashboard shows Online / Ready / Healthy with DB latency and version |
| Database down | A 503 readiness response is shown as Degraded / Down with the error type |
| API unreachable | Network failure shows Unreachable and a helpful message |
| 404 | Unknown routes render the not-found page |

**Manual verification against running servers** (uvicorn + Vite dev server + PostgreSQL 16):

- `alembic upgrade head` applied revision `0001`.
- `GET /api/v1/health/ready` → 200 with DB latency; response carried an `x-request-id` header, and the matching JSON log line included the same `request_id`.
- Stopped PostgreSQL → `/health/live` stayed 200, `/health/ready` returned 503 with `ConnectionRefusedError`. Restarted PostgreSQL → `/health/ready` returned 200 again **without restarting the API** (connection pool pre-ping).
- Dashboard rendered in Chromium (Playwright) with live data: see `docs/screenshots-dashboard.png`.

## Not yet verified

- **Docker Compose stack.** `docker compose config` validates, but the images could not be built in the environment Phase 1 was developed in (no access to Docker Hub). The CI `docker` job builds the images and checks health on every push; run `docker compose up --build` locally to confirm on your machine.
- The CI workflow itself has not run yet: it runs on the first push to GitHub.
