# NexusAI

[![CI](https://github.com/AdityaTiwarime/nexusai/actions/workflows/ci.yml/badge.svg)](https://github.com/AdityaTiwarime/nexusai/actions/workflows/ci.yml)

**Enterprise Intelligence & Autonomous Workflow Platform.** A portfolio implementation of the kind of system a Forward Deployed Engineer builds: it connects scattered company knowledge, business applications and workflows into one secure, AI-powered platform with cited answers, approval-gated AI agents and full observability.

> **Status: Phases 1–2 of 10 complete** (foundation; authentication, roles and multi-tenancy). This README only describes what is actually built and tested. See [`docs/roadmap.md`](docs/roadmap.md) for the plan and live feature status.

| User management | Audit log |
|---|---|
| ![Users](docs/screenshots/users.png) | ![Audit logs](docs/screenshots/audit-logs.png) |

## The business problem

A 5,000-person company keeps knowledge in documents, a CRM, a ticketing system and internal databases. Employees waste time searching across them, and repetitive workflows (create a ticket, notify a customer) are done by hand. NexusAI's target product lets employees:

1. Search internal information and ask questions about documents.
2. Get evidence-based answers **with citations**, or a clear "the documents don't support an answer".
3. Run multi-step workflows through AI agents, with **human approval** before any sensitive action.
4. Connect business systems (Salesforce, ServiceNow, Microsoft Graph) through a connector framework.
5. Monitor system health and investigate failures with AI assistance.

All data is synthetic. No real customer data or credentials are used.

## What works today

**Phase 2: secure multi-tenant access**

- **Organizations as tenants.** Sign up creates an organization with you as its admin; every query is scoped to the caller's organization, and another organization's records return **404** even if you know their IDs.
- **Authentication**: Argon2id password hashing, 15-minute JWT access tokens, and **rotating refresh tokens in an httpOnly cookie** with **reuse detection** (replaying an old token ends the whole session).
- **Role-based access control** enforced on the server: org admin, manager, employee, plus a platform super admin created only via CLI. Role changes and deactivations take effect **immediately**.
- **User management** UI and API: add users, change roles, deactivate (which also ends their sessions).
- **Audit log**: logins, failed logins, token-reuse alerts and user changes, each with actor, IP and request ID; viewable and filterable by org admins.
- Details: [`docs/security.md`](docs/security.md) and [`docs/database-design.md`](docs/database-design.md).

**Phase 1: foundation**


- **FastAPI backend**: app factory, validated environment configuration, JSON structured logging, request IDs on every response and log line, safe 500 handling that never leaks internals.
- **PostgreSQL** via async SQLAlchemy 2.0 + asyncpg, with **Alembic** migrations.
- **Health checks**: `/health/live` (process up) and `/health/ready` (database reachable, with latency; returns 503 when it isn't). The API recovers automatically when the database comes back.
- **React + TypeScript dashboard** (Vite, Tailwind, React Router, TanStack Query) showing live system status, with the navigation for all upcoming modules.
- **Docker Compose** for PostgreSQL (pgvector image), backend and frontend (nginx), with health-checked startup ordering.
- **GitHub Actions CI**: lint, backend tests against real PostgreSQL, frontend type check/tests/build, dependency audits, and a full Docker stack health check.
- **Tests**: 59 backend + 14 frontend, all passing, plus a browser end-to-end run. Details in [`docs/testing.md`](docs/testing.md).

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
| Backend | Python 3.13, FastAPI, Pydantic, SQLAlchemy 2 (async), Alembic, PyJWT, Argon2 | LangGraph, LangChain, background workers |
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

- App: http://localhost:5173 (click **Create an organization** to get started)
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

## API overview

Interactive docs: http://localhost:8000/docs (disabled in production).

| Method | Path | Who | Description |
|---|---|---|---|
| GET | `/api/v1/health/live` | public | Process is running |
| GET | `/api/v1/health/ready` | public | Dependencies OK; 503 with per-check details if not |
| GET | `/api/v1/system/info` | public | App name, version, environment |
| POST | `/api/v1/auth/register` | public | Create an organization + its first admin, sign in |
| POST | `/api/v1/auth/login` | public | Sign in (access token + refresh cookie) |
| POST | `/api/v1/auth/refresh` | refresh cookie | Rotate the refresh token, get a new access token |
| POST | `/api/v1/auth/logout` | refresh cookie | End the session |
| GET | `/api/v1/auth/me` | signed in | Current user and organization |
| GET | `/api/v1/organizations/current` | org member | Caller's organization |
| GET | `/api/v1/users` | manager, org admin | Users in the caller's organization |
| GET | `/api/v1/users/{id}` | manager, org admin | One user (404 if in another organization) |
| POST | `/api/v1/users` | org admin | Add a user |
| PATCH | `/api/v1/users/{id}` | org admin | Change name, role or active status |
| GET | `/api/v1/audit-logs` | org admin | Organization's audit events (paginated, filterable) |
| GET | `/api/v1/admin/organizations` | super admin | All organizations |

**Creating the platform super admin** (there is deliberately no API for this):

```bash
docker compose exec backend python -m app.cli create-superadmin --email ops@example.com --name "Platform Ops"
```

## Configuration

All backend settings are environment variables with the `NEXUS_` prefix, validated at startup. See [`.env.example`](.env.example) for every variable and what it does.

## Documentation

- [`docs/roadmap.md`](docs/roadmap.md): feasibility, phases, database design, milestones, feature status
- [`docs/architecture.md`](docs/architecture.md): architecture diagrams and decisions
- [`docs/security.md`](docs/security.md): authentication, RBAC, tenant isolation, threat model, known limitations
- [`docs/database-design.md`](docs/database-design.md): ER diagram, constraints, indexes, design decisions
- [`docs/testing.md`](docs/testing.md): test strategy and actual results

## Project structure

```
nexusai/
├── backend/            FastAPI app, Alembic migrations, tests
├── frontend/           React + TypeScript app, nginx config
├── infra/              Postgres init scripts (observability config in later phases)
├── docs/               Architecture, roadmap, security, database design, testing
├── .github/workflows/  CI pipeline
└── docker-compose.yml
```
