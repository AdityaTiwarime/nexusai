# NexusAI

[![CI](https://github.com/AdityaTiwarime/nexusai/actions/workflows/ci.yml/badge.svg)](https://github.com/AdityaTiwarime/nexusai/actions/workflows/ci.yml)

**Enterprise Intelligence & Autonomous Workflow Platform.** A portfolio implementation of the kind of system a Forward Deployed Engineer builds: it connects scattered company knowledge, business applications and workflows into one secure, AI-powered platform with cited answers, approval-gated AI agents and full observability.

> **Status: Phases 1–6 and the first part of Phase 10 complete** (foundation; authentication, roles and multi-tenancy; document intelligence; RAG engine and AI Chat; AI agents with human approval; integrations; cloud deployment and security hardening). Phases 7–9 (events, observability, incident investigation) are next. This README only describes what is actually built and tested. See [`docs/roadmap.md`](docs/roadmap.md) for the plan and live feature status.

| Integrations: health and call log | Agent run across ServiceNow, Salesforce and email |
|---|---|
| ![Integrations](docs/screenshots/integrations.png) | ![Agent run](docs/screenshots/workflow-integrations.png) |
| **Approval: exactly what will run** | **AI Chat with citations** |
| ![Approvals](docs/screenshots/approvals.png) | ![AI Chat](docs/screenshots/chat.png) |
| **Hybrid search** | **Completed agent run** |
| ![Search](docs/screenshots/search.png) | ![Agent run](docs/screenshots/workflow-completed.png) |
| **Documents** | **Document detail** |
| ![Documents](docs/screenshots/documents.png) | ![Document detail](docs/screenshots/document-detail.png) |
| **User management** | **Audit log** |
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

**Phase 10 (part 1): cloud deployment and security hardening**

- **AWS with Terraform**: VPC, a hardened EC2 server (no SSH, IMDSv2 with hop limit 1, encrypted disks, separate data volume), generated secrets in Secrets Manager with KMS, least-privilege IAM, private TLS-only S3 backups, and a GitHub OIDC deploy role with no stored AWS keys. **Tested with mocked AWS providers (`tofu test`) and Checkov; not applied to a real AWS account.**
- **Production stack**: Caddy with automatic HTTPS and HSTS, strict Content-Security-Policy, read-only non-root containers, no published database or API ports, and the database and Redis on an internal-only network.
- **CI/CD with automatic rollback**: images published per commit; `deploy.sh` health-checks each release through HTTPS and goes back to the previous one if it isn't healthy (tested for real in CI).
- **Kubernetes**: Kustomize manifests deployed to a **kind** cluster in CI under the restricted Pod Security Standard, with **network policies proven to be enforced** and a bad release rolled back.
- **Security scans in CI**: Trivy (images), OWASP ZAP baseline (headers), Checkov (Terraform and Kubernetes), shellcheck, plus the existing pip-audit and npm audit. Threat model: [`docs/threat-model.md`](docs/threat-model.md). Details: [`docs/deployment.md`](docs/deployment.md).

**Phase 6: integrations**

- **Connectors for ServiceNow, Salesforce and email** (Microsoft Graph style), tested against bundled **mock services with synthetic data** that run as their own container. Approved agent actions now create real incidents in the mock ServiceNow, look up the customer in the mock Salesforce, and actually send the email.
- **Built for unreliable systems**: timeouts, retries with exponential backoff and `Retry-After`, idempotency keys (a retry never creates a duplicate incident or email), and a per-call log with attempts and latency.
- **Signed webhooks**: resolving an incident in ServiceNow updates the NexusAI ticket. Webhooks are verified with HMAC, protected against replays, de-duplicated, and their secret can be rotated.
- **Security**: credentials encrypted at rest and never returned by the API; integration URLs restricted to an allowlist (SSRF protection).
- **Integrations page**: connect with one click to the bundled mocks, test connections, and see health and recent calls. Details: [`docs/integrations-design.md`](docs/integrations-design.md).

**Phase 5: AI agents with human approval**

- **Describe a task in plain language**, for example "Customer jane@globex.com can't log in. Open a high priority ticket and email her." The agent plans the steps as a **LangGraph** workflow.
- **Read-only steps run at once; anything that creates or sends waits for a human.** A manager sees the exact actions and arguments, then approves or rejects with a comment.
- **Separation of duties**: nobody can approve their own request. Requests expire after 24 hours, and two simultaneous approvals can't both execute.
- **Safe by construction**: agents can only use three registered tools with strict schemas: search documents, create ticket, email customer. Any plan with an unknown tool or extra arguments is rejected as a whole.
- **Reliable execution**: each action runs in a savepoint, is idempotent per step, and is verified afterwards. Failed runs can be retried without duplicating anything. Every step is in the audit log.
- Emails are **recorded in an outbox, not sent**, until the email connector arrives in Phase 6. Details: [`docs/agents-design.md`](docs/agents-design.md).

**Phase 4: RAG engine and AI Chat**

- **Ask questions in plain language** and get answers with **numbered citations**. Each citation links to the exact passage in the document, highlighted.
- **"I couldn't find this" instead of guessing.** When the documents don't support an answer, NexusAI says so. In LLM mode, an answer with no valid citation is rejected in code, not just in the prompt.
- **Hybrid retrieval**: pgvector similarity search plus PostgreSQL full-text search, merged with Reciprocal Rank Fusion and re-ranked.
- **Permissions inside the search query**: answers only ever use documents the asker is allowed to open. If a document is later restricted or deleted, old citations to it are hidden.
- **Two answer modes**: an offline mode that quotes the documents (default, no API key needed) and an OpenAI-compatible LLM mode with prompt-injection guards, token tracking and clear errors when the provider is down.
- **Private conversations** with follow-up questions, plus a **Search** page showing ranked passages with highlights.
- **Measured, not claimed**: a small benchmark runs in CI. Offline mode scores 22/22 on questions worded like the documents and refuses 6/6 unanswerable ones, but answers 0/8 paraphrased questions. It refuses those rather than answering wrongly. Full results and caveats: [`docs/ai-design.md`](docs/ai-design.md#evaluation).

**Phase 3: document intelligence**

- **Upload** PDF, Word, text, CSV and Markdown files. Files are checked by **content, not just extension** (a renamed `.exe` is rejected), with a size limit and a zip-bomb guard. **Duplicate uploads are detected** by SHA-256 within an organization.
- **Background processing** in a separate worker: text extraction (with **PDF page numbers**), structure-aware **chunking with overlap**, and **embeddings stored in PostgreSQL with pgvector** next to a full-text index, ready for hybrid search in Phase 4.
- **Reliable job queue** in PostgreSQL: retries with exponential backoff, recovery of jobs from crashed workers, several workers in parallel without double-processing, and outdated jobs skipped when a newer version is uploaded.
- **Versioning**: upload a new version, keep older files downloadable, chunks are replaced atomically.
- **Per-document visibility** (everyone / managers / admins only), enforced in the database query. Hidden documents behave like missing ones.
- **Embedding providers behind one interface**: an offline deterministic provider (default; lexical, **not semantic**) and an OpenAI provider for real semantic embeddings.
- **Login and sign-up rate limiting** with Redis (per IP and per account); if Redis is down, logins keep working.
- **Documents UI**: upload, live status, failure reasons, status filters and search, versions, extracted chunks, reprocess, delete.
- Details: [`docs/ai-design.md`](docs/ai-design.md) and [`docs/operations.md`](docs/operations.md).

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
- **Tests** (all phases): 239 backend + 45 frontend, all passing, plus browser end-to-end runs and a CI smoke test that uploads a document, asks questions, runs an approved agent task, and drives the mock ServiceNow, Salesforce and email services through the Docker stack. Details in [`docs/testing.md`](docs/testing.md).

## Architecture

```mermaid
flowchart LR
    UI[React SPA] -->|/api| NGINX[nginx] --> API[FastAPI]
    API --> PG[(PostgreSQL + pgvector)]
    API --> REDIS[(Redis<br/>rate limits)]
    API --> FILES[(Uploads volume)]
    WORKER[Ingestion worker] -->|claims jobs| PG
    WORKER --> FILES
    WORKER --> EMB[Embedding provider]
    API --> ANS[Answer provider<br/>extractive or LLM]
    API -. later .-> KAFKA[(Kafka)]
```

Full diagrams, component responsibilities and design decisions: [`docs/architecture.md`](docs/architecture.md).

## Tech stack

| Layer | Now (Phase 10, part 1) | Planned |
|---|---|---|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, React Router, TanStack Query | — |
| Backend | Python 3.13, FastAPI, Pydantic, SQLAlchemy 2 (async), Alembic, PyJWT, Argon2, pypdf, python-docx, httpx, LangGraph, cryptography (Fernet); background worker; hybrid RAG with an OpenAI-compatible LLM client; connector framework | Kafka event log |
| Data | PostgreSQL 16 + pgvector, Redis | Kafka |
| Infra | Docker, Docker Compose, Caddy, GitHub Actions; Terraform/OpenTofu for AWS (EC2, VPC, IAM, KMS, Secrets Manager, S3, SSM; mock-tested); Kubernetes (Kustomize, kind in CI) | Real AWS deployment, managed database (RDS) |
| Observability | Structured logs, request IDs | OpenTelemetry, Prometheus, Grafana |
| Testing | pytest, Vitest, Testing Library, `tofu test`, kubeconform, Checkov, Trivy, OWASP ZAP, shellcheck | Playwright, Locust |

## Quick start (Docker)

Prerequisites: Docker Desktop (or Docker Engine with Compose v2).

```bash
git clone https://github.com/<your-username>/nexusai.git
cd nexusai
cp .env.example .env
docker compose up --build
```

Then open:

- App: http://localhost:5173 (click **Create an organization**, upload files in **Documents**, then ask questions in **AI Chat**). To try approvals, add a manager in **Users**: nobody can approve their own agent request. In **Integrations**, click "Fill in the bundled mock service" to connect the mock ServiceNow, Salesforce and email.
- Mock services: http://localhost:9000/docs (inspect with `/_mock/state`)
- Optional: set `NEXUS_OPENAI_API_KEY` and `NEXUS_ANSWER_PROVIDER=openai` (and `NEXUS_EMBEDDING_PROVIDER=openai`) in `.env` for LLM answers and semantic search. If you change the embedding provider, reprocess your documents.
- API docs (Swagger): http://localhost:8000/docs
- Readiness: http://localhost:8000/api/v1/health/ready

Stop with `Ctrl+C`, or `docker compose down` (add `-v` to also delete the database volume).

## Local development (without Docker for the app)

```bash
# 1. Database and Redis only, in Docker
docker compose up -d db redis

# 2. Backend (terminal 1)
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp ../.env.example .env
alembic upgrade head
uvicorn app.asgi:app --reload --port 8000

# 2b. Worker (another terminal, same folder and venv): processes uploads
python -m app.worker

# 3. Frontend (terminal 2)
cd frontend
npm install
npm run dev                        # http://localhost:5173, proxies /api to :8000
```

## Running tests

```bash
# Backend (needs the db and redis containers running)
cd backend
export NEXUS_TEST_DATABASE_URL=postgresql+asyncpg://nexus:nexus@localhost:5432/nexusai_test
export NEXUS_TEST_REDIS_URL=redis://localhost:6379/15
pytest -v

# Frontend
cd frontend
npm test

# Deploy scripts, Terraform (mocked AWS, no credentials) - see docs/testing.md
pytest deploy/tests
cd infra/terraform/aws && tofu init -backend=false && tofu test
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
| POST | `/api/v1/documents` | manager, org admin | Upload (multipart: `file`, optional `title`, `min_role`) |
| GET | `/api/v1/documents` | org member | Visible documents (filters: `status`, `q`; paginated; counts per status) |
| GET | `/api/v1/documents/{id}` | org member | Document with versions (404 if hidden or another org's) |
| GET | `/api/v1/documents/{id}/chunks` | org member | Extracted chunks with page ranges |
| GET | `/api/v1/documents/{id}/download` | org member | Original file (`?version=n` for older ones) |
| POST | `/api/v1/documents/{id}/versions` | uploader, org admin | Upload a new version |
| POST | `/api/v1/documents/{id}/reprocess` | uploader, org admin | Queue processing again |
| PATCH | `/api/v1/documents/{id}` | uploader, org admin | Rename or change visibility |
| DELETE | `/api/v1/documents/{id}` | uploader, org admin | Delete the document, its versions, chunks and files |
| POST | `/api/v1/search` | org member | Hybrid search over visible documents (`query`, `limit`, optional `document_ids`) |
| POST | `/api/v1/chat/ask` | org member | Ask a question (optionally in an existing conversation); answer with citations or "no evidence" |
| GET | `/api/v1/conversations` | owner | Your conversations, most recent first |
| GET | `/api/v1/conversations/{id}` | owner | Messages, with citations re-checked against current permissions |
| PATCH / DELETE | `/api/v1/conversations/{id}` | owner | Rename / delete a conversation |
| GET | `/api/v1/agents/tools` | org member | The tools agents may use, with schemas and approval requirement |
| POST | `/api/v1/agents/runs` | org member | Start an agent task; read-only steps run, others wait for approval |
| GET | `/api/v1/agents/runs`, `/agents/runs/{id}` | owner; managers and admins see all | Runs with steps, results and approval state |
| POST | `/api/v1/agents/runs/{id}/retry` | manager, org admin | Retry a failed approved run (completed steps aren't repeated) |
| GET | `/api/v1/approvals`, `/approvals/{id}` | manager, org admin | Approval queue (`?status=pending`), with the exact actions |
| POST | `/api/v1/approvals/{id}/approve`, `/reject` | manager, org admin (not the requester) | Decide, with an optional comment |
| GET | `/api/v1/tickets`, `/api/v1/outbox` | org member / manager, org admin | Records created by approved actions (with ServiceNow / email references) |
| GET | `/api/v1/integrations` | manager, org admin | Connections with health; secrets are never returned |
| POST / PATCH / DELETE | `/api/v1/integrations`, `/integrations/{id}` | org admin | Connect, update (write-only secrets, enable/disable), disconnect |
| POST | `/api/v1/integrations/{id}/test`, `/rotate-webhook-secret` | org admin | Health check; new webhook secret (shown once) |
| GET | `/api/v1/integrations/{id}/calls` | manager, org admin | Recent calls: operation, outcome, status, attempts, latency |
| POST | `/api/v1/webhooks/servicenow/{id}` | signed by ServiceNow | Incident updates (HMAC + timestamp verified, de-duplicated) |

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
- [`docs/integrations-design.md`](docs/integrations-design.md): connectors, retries, idempotency, credentials, webhooks, honest limits
- [`docs/agents-design.md`](docs/agents-design.md): agent workflow, tools, planners, approval rules, reliability
- [`docs/ai-design.md`](docs/ai-design.md): ingestion, retrieval, answers with citations, prompt-injection handling, evaluation results, limitations
- [`docs/deployment.md`](docs/deployment.md): AWS (Terraform), production stack, CI/CD with rollback, backups, Kubernetes, what CI verifies, honest limits
- [`docs/threat-model.md`](docs/threat-model.md): STRIDE threat model across all trust boundaries, residual risks
- [`docs/operations.md`](docs/operations.md): services, worker, queries, troubleshooting
- [`docs/testing.md`](docs/testing.md): test strategy and actual results

## Project structure

```
nexusai/
├── backend/            FastAPI app (+ mock enterprise services), Alembic migrations, tests
├── frontend/           React + TypeScript app, nginx config (security headers)
├── deploy/             Production compose, Caddyfile, deploy/rollback + backup scripts, Kubernetes manifests
├── infra/              Postgres init scripts, Terraform for AWS (infra/terraform/aws)
├── scripts/            End-to-end smoke test used by CI
├── docs/               Architecture, roadmap, security, database design, testing
├── .github/workflows/  CI pipeline, publish-and-deploy workflow
└── docker-compose.yml
```
