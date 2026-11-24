# NexusAI

**Enterprise Intelligence & Autonomous Workflow Platform.** A portfolio implementation of the kind of system a Forward Deployed Engineer builds: it connects scattered company knowledge, business applications and workflows into one secure, AI-powered platform with cited answers, approval-gated AI agents and full observability.

> **Status: Phase 1 of 10 (Foundation) complete.** This README only describes what is actually built and tested. See [`docs/roadmap.md`](docs/roadmap.md) for the plan and live feature status.

![Dashboard](docs/screenshots-dashboard.png)

## The business problem

A 5,000-person company keeps knowledge in documents, a CRM, a ticketing system and internal databases. Employees waste time searching across them, and repetitive workflows (create a ticket, notify a customer) are done by hand. NexusAI's target product lets employees:

1. Search internal information and ask questions about documents.
2. Get evidence-based answers **with citations**, or a clear "the documents don't support an answer".
3. Run multi-step workflows through AI agents, with **human approval** before any sensitive action.
4. Connect business systems (Salesforce, ServiceNow, Microsoft Graph) through a connector framework.
5. Monitor system health and investigate failures with AI assistance.

All data is synthetic. No real customer data or credentials are used.

## What works today (Phase 1)

- **FastAPI backend**: app factory, validated environment configuration, JSON structured logging, request IDs on every response and log line, safe 500 handling that never leaks internals.
- **PostgreSQL** via async SQLAlchemy 2.0 + asyncpg, with **Alembic** migrations.
- **Health checks**: `/health/live` (process up) and `/health/ready` (database reachable, with latency; returns 503 when it isn't). The API recovers automatically when the database comes back.
- **React + TypeScript dashboard** (Vite, Tailwind, React Router, TanStack Query) showing live system status, with the navigation for all upcoming modules.
- **Docker Compose** for PostgreSQL (pgvector image), backend and frontend (nginx), with health-checked startup ordering.
- **GitHub Actions CI**: lint, backend tests against real PostgreSQL, frontend type check/tests/build, dependency audits, and a full Docker stack health check.
- **Tests**: 17 backend + 4 frontend, all passing. Details in [`docs/testing.md`](docs/testing.md).

## Architecture

```mermaid
flowchart LR
    UI[React SPA] -->|/api| NGINX[nginx] --> API[FastAPI]
    API --> PG[(PostgreSQL + pgvector)]
    API -. later .-> REDIS[(Redis)] & KAFKA[(Kafka)] & LLM[LLM providers]
```

Full diagrams, component responsibilities and design decisions: [`docs/architecture.md`](docs/architecture.md).

## Tech stack

| Layer | Now (Phase 1) | Planned |
|---|---|---|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, React Router, TanStack Query | — |
| Backend | Python 3.13, FastAPI, Pydantic, SQLAlchemy 2 (async), Alembic | LangGraph, LangChain, background workers |
| Data | PostgreSQL 16 | pgvector, Redis, Kafka |
| Infra | Docker, Docker Compose, GitHub Actions | AWS, Terraform (if deployed) |
| Observability | Structured logs, request IDs | OpenTelemetry, Prometheus, Grafana |
| Testing | pytest, Vitest, Testing Library | Playwright, Locust |

## Quick start (Docker)

Prerequisites: Docker Desktop (or Docker Engine with Compose v2).

```bash
git clone https://github.com/<your-username>/nexusai.git
cd nexusai
cp .env.example .env
docker compose up --build
```

Then open:

- Dashboard: http://localhost:5173
- API docs (Swagger): http://localhost:8000/docs
- Readiness: http://localhost:8000/api/v1/health/ready

Stop with `Ctrl+C`, or `docker compose down` (add `-v` to also delete the database volume).

## Local development (without Docker for the app)

```bash
# 1. Database only, in Docker
docker compose up -d db

# 2. Backend (terminal 1)
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp ../.env.example .env
alembic upgrade head
uvicorn app.asgi:app --reload --port 8000

# 3. Frontend (terminal 2)
cd frontend
npm install
npm run dev                        # http://localhost:5173, proxies /api to :8000
```

## Running tests

```bash
# Backend (needs the db container running)
cd backend
export NEXUS_TEST_DATABASE_URL=postgresql+asyncpg://nexus:nexus@localhost:5432/nexusai_test
pytest -v

# Frontend
cd frontend
npm test
```

## API overview (Phase 1)

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/health/live` | Process is running (never checks dependencies) |
| GET | `/api/v1/health/ready` | Dependencies OK; 503 with per-check details if not |
| GET | `/api/v1/system/info` | App name, version, environment |

## Configuration

All backend settings are environment variables with the `NEXUS_` prefix, validated at startup. See [`.env.example`](.env.example) for every variable and what it does.

## Documentation

- [`docs/roadmap.md`](docs/roadmap.md): feasibility, phases, database design, milestones, feature status
- [`docs/architecture.md`](docs/architecture.md): architecture diagrams and decisions
- [`docs/testing.md`](docs/testing.md): test strategy and actual results

## Project structure

```
nexusai/
├── backend/            FastAPI app, Alembic migrations, tests
├── frontend/           React + TypeScript app, nginx config
├── infra/              Postgres init scripts (observability config in later phases)
├── docs/               Architecture, roadmap, testing
├── .github/workflows/  CI pipeline
└── docker-compose.yml
```
