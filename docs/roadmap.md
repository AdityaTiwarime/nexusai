# NexusAI: Plan, Feasibility and Roadmap

This document is the "before we write code" plan for NexusAI. It is kept up to date as phases land, so it doubles as the honest status of the project.

---

## 1. Feasibility assessment

**Verdict: feasible as a portfolio implementation, if built in phases and scoped honestly.**

| Area | Feasibility | Notes |
|---|---|---|
| Auth, RBAC, multi-tenancy | High | Standard FastAPI + JWT + row-level tenant scoping. Tenant isolation is the part to test hardest. |
| Document ingestion + pgvector | High | Text extraction for PDF/DOCX/TXT/CSV/MD is well supported in Python. Background workers keep uploads non-blocking. |
| Hybrid RAG (vector + keyword + rerank) | High | pgvector + PostgreSQL full-text search covers both retrieval modes in one database. Reranking can use a local cross-encoder or an LLM. |
| LangGraph agents + human approval | Medium | The state machine is straightforward; the hard part is safe tool design (no free-form SQL, no shell). |
| Enterprise connectors | Medium | Built against **mock** Salesforce / ServiceNow / Microsoft Graph services with synthetic data. Real accounts are out of scope unless actually configured. |
| Kafka | Medium | Justified only for the event log between ingestion, agents and audit. Redis queues cover simpler background jobs. Kafka is optional in local dev. |
| Observability (OTel, Prometheus, Grafana) | High | All run locally in Docker Compose. |
| AWS deployment | Medium | Designed and documented; Terraform only if an AWS account is actually used. |
| AI incident investigation | Medium | Works on synthetic telemetry. Must separate observed evidence from AI hypotheses. |

**Main risks and mitigations**

- *Scope creep*: 10 modules is a lot. Each phase ends with a working, tested slice, so the project is useful even if later phases slip.
- *LLM cost and availability*: a provider interface with a deterministic **fake provider** keeps tests and demos runnable without API keys.
- *Over-engineering*: each technology is introduced only in the phase where it solves a real requirement (see the phase table).
- *Honesty on the resume*: the feature verification matrix (Section 9) is filled in only from real test runs.

---

## 2. Final architecture

See [`architecture.md`](architecture.md) for the full diagram and component responsibilities.

Summary:

- **Modular monolith** in FastAPI, split into bounded modules (auth, documents, rag, agents, integrations, workflows, audit, observability). One deployable keeps local development simple; module boundaries keep a later split into services possible.
- **Separate worker process** (same codebase) for ingestion, embeddings and long-running agent runs.
- **PostgreSQL + pgvector** as the system of record and vector store; **Redis** for queues, rate limits and caching; **Kafka** only for the domain event log.
- **React + TypeScript** single-page app talking to the REST API.
- **OpenTelemetry** traces and metrics from API, worker and AI calls into Prometheus/Grafana (plus a trace backend).

---

## 3. Repository structure (target)

```
nexusai/
├── backend/
│   ├── app/
│   │   ├── main.py                 # app factory, middleware, routers
│   │   ├── core/                   # config, logging, security, middleware
│   │   ├── db/                     # engine, session, base model
│   │   ├── api/                    # HTTP routers (thin)
│   │   ├── modules/
│   │   │   ├── auth/               # users, orgs, roles, JWT, RBAC   (Phase 2)
│   │   │   ├── documents/          # upload, parsing, chunking        (Phase 3)
│   │   │   ├── rag/                # retrieval, rerank, answer        (Phase 4)
│   │   │   ├── agents/             # LangGraph graphs, tools          (Phase 5)
│   │   │   ├── workflows/          # approvals, workflow runs         (Phase 5)
│   │   │   ├── integrations/       # connector framework, mocks       (Phase 6)
│   │   │   ├── events/             # event schemas, bus, DLQ          (Phase 7)
│   │   │   ├── audit/              # audit log                        (Phase 2+)
│   │   │   └── incidents/          # AI incident investigation        (Phase 9)
│   │   ├── llm/                    # provider interface + providers   (Phase 4)
│   │   └── worker/                 # background worker entrypoint     (Phase 3)
│   ├── alembic/                    # migrations
│   ├── tests/                      # unit / integration / reliability
│   ├── Dockerfile
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── api/                    # typed API client
│   │   ├── components/             # design system + layout
│   │   ├── pages/                  # one folder per page
│   │   └── test/
│   ├── Dockerfile
│   └── package.json
├── infra/                          # prometheus, grafana, otel collector, terraform (later)
├── docs/
├── .github/workflows/ci.yml
├── docker-compose.yml
└── .env.example
```

---

## 4. Database design proposal

All business tables carry `organization_id` (tenant key) and are queried through a tenant-scoped repository layer. Full ER diagram and design decisions: [`database-design.md`](database-design.md).

| Table | Key columns | Introduced |
|---|---|---|
| organizations | id, name, slug (unique), is_active, created_at | Phase 2 ✅ |
| users | id, organization_id FK, email (unique), password_hash, role, is_active | Phase 2 ✅ |
| (roles) | stored as a constrained `role` column on users, see `database-design.md` | Phase 2 ✅ |
| refresh_tokens | id, user_id, family_id, token_hash, expires_at, revoked_at | Phase 2 ✅ |
| audit_logs | id, organization_id, actor_id, action, target, ip, request_id, details JSONB, created_at | Phase 2 ✅ |
| documents | id, organization_id, uploaded_by, filename, file_type, sha256 (unique per org), status, current_version | Phase 3 |
| document_versions | id, document_id, version, storage_path, created_at | Phase 3 |
| document_chunks | id, document_version_id, organization_id, chunk_index, content, tsv (tsvector), embedding (vector) | Phase 3 |
| conversations / messages | id, organization_id, user_id; message role, content, citations JSONB | Phase 4 |
| agent_definitions / tool_definitions | name, description, allowed_roles, is_mutating | Phase 5 |
| agent_executions / workflow_runs | id, state, plan JSONB, steps JSONB, started/finished timestamps | Phase 5 |
| approval_requests | id, workflow_run_id, action JSONB, status, requested_by, decided_by, expires_at | Phase 5 |
| integrations / integration_credentials | connector type, config JSONB; credentials encrypted at rest | Phase 6 |
| system_events | id, event_type, idempotency_key (unique), payload JSONB, status | Phase 7 |
| incidents | id, organization_id, title, timeline JSONB, evidence JSONB, hypotheses JSONB | Phase 9 |

Key decisions:

- **UUID primary keys** (no guessable sequential IDs across tenants).
- **Composite indexes starting with `organization_id`** on every tenant table.
- **Unique `(organization_id, sha256)`** on documents for duplicate detection.
- **HNSW index** on `document_chunks.embedding` and a **GIN index** on `tsv` for hybrid retrieval.
- **Unique `idempotency_key`** on events so replays never create duplicate business actions.

---

## 5. Development phases

| Phase | Goal | Key technologies introduced |
|---|---|---|
| **1. Foundation** | Repo, FastAPI, React, PostgreSQL, Docker Compose, config, health checks, tests, CI, docs | FastAPI, SQLAlchemy (async), Alembic, Vite, React, Tailwind, TanStack Query |
| 2. Auth & tenancy | Registration, login, JWT + refresh, RBAC, orgs, tenant isolation, audit log | passlib/argon2, PyJWT |
| 3. Document intelligence | Upload, validation, parsing, chunking, embeddings, status tracking, dedupe, worker | pgvector, Redis, background worker |
| 4. RAG engine | Hybrid retrieval, permission filtering, reranking, citations, "no evidence" handling, conversations | LLM provider interface, embeddings |
| 5. Agents + approvals | LangGraph state machine, safe tools, human-in-the-loop approvals | LangGraph |
| 6. Integrations | Connector framework, mock Salesforce/ServiceNow/Graph, webhooks, retries, rate limits | httpx, HMAC webhook verification |
| 7. Events | Event schemas, idempotency, retries, dead-letter handling | Kafka (where justified) |
| 8. Observability | Traces, metrics, AI metrics (tokens, cost, latency), dashboards | OpenTelemetry, Prometheus, Grafana |
| 9. Incident investigation | Evidence gathering, timeline, hypotheses, runbook retrieval, approval-gated remediation | builds on 4, 5, 8 |
| 10. Enterprise UI + hardening | All 14 pages, load tests, security scans, AWS deployment design, interview pack | Locust, Trivy/pip-audit |

---

## 6. Dependencies and prerequisites

On your machine:

- **Docker Desktop** (or Docker Engine + Compose v2)
- **Python 3.12+** (3.13 is used in CI)
- **Node.js 20+** (22 recommended)
- **Git** and a GitHub account
- Later phases: an LLM API key (OpenAI or Anthropic). Not needed until Phase 4; a fake provider keeps everything runnable without one.

---

## 7. Milestones

| Milestone | Done when |
|---|---|
| M1: Foundation | `docker compose up` starts db, backend, frontend; health page shows the database is reachable; CI green |
| M2: Secure multi-tenant API | Tenant isolation tests prove org A cannot read org B's data |
| M3: Documents searchable | A PDF upload is parsed, chunked and embedded in the background, with status visible in the UI |
| M4: Cited answers | Questions return answers with clickable citations; unanswerable questions say so |
| M5: Safe agents | "Create ticket + notify customer" runs end to end with approval |
| M6: Connected systems | Mock connectors pass failure tests (timeouts, 429s, bad webhooks) |
| M7: Reliable events | Duplicate events are processed exactly once at the business level |
| M8: Observable | One request is traceable through API → RAG → agent → connector |
| M9: Investigations | Incident module produces a timeline with evidence clearly separated from hypotheses |
| M10: Portfolio ready | Verification matrix complete, README with screenshots, demo script, resume bullets from verified features |

---

## 8. Feature status

| Feature | Status |
|---|---|
| Repository foundation | ✅ Implemented (Phase 1) |
| FastAPI app factory, config, structured logging, request IDs | ✅ Implemented (Phase 1) |
| PostgreSQL connection + Alembic migrations | ✅ Implemented (Phase 1) |
| Liveness / readiness health checks | ✅ Implemented (Phase 1) |
| React + TypeScript dashboard shell with live system status | ✅ Implemented (Phase 1) |
| Docker Compose (db, backend, frontend) | ✅ Implemented (Phase 1), verified in CI |
| GitHub Actions CI (lint, tests, dependency audit, Docker stack health) | ✅ Implemented (Phase 1) |
| Registration, login, logout, JWT access tokens, rotating refresh tokens with reuse detection | ✅ Implemented (Phase 2) |
| Roles (super admin, org admin, manager, employee) enforced server-side | ✅ Implemented (Phase 2) |
| Multi-tenant isolation (organization-scoped queries, 404 for foreign IDs) | ✅ Implemented (Phase 2) |
| User management (add, change role, deactivate) | ✅ Implemented (Phase 2) |
| Audit log (API + UI) | ✅ Implemented (Phase 2) |
| Super-admin CLI | ✅ Implemented (Phase 2) |
| Login rate limiting | ⏳ Planned (Phase 3, with Redis) |
| Email verification, password reset, SSO/MFA | ⏳ Planned (Phase 6) |
| Everything else | ⏳ Planned (Phases 3–10) |

## 9. Feature verification matrix

Filled in only from real test runs and evidence. See `docs/testing.md`.

| Feature | Implemented | Tested | Evidence |
|---|---|---|---|
| Health checks + DB connectivity | Yes | Yes | `backend/tests/test_health.py` |
| Migrations apply cleanly | Yes | Yes | `backend/tests/test_migrations.py` |
| Frontend status page | Yes | Yes | `frontend/src/test/` |
| RAG pipeline | No | No | — |
| Agent orchestration | No | No | — |
| Authentication (JWT + refresh rotation) | Yes | Yes | `backend/tests/test_auth.py`, `frontend/src/test/auth.test.tsx` |
| RBAC / tenant isolation | Yes | Yes | `backend/tests/test_rbac_and_tenancy.py` |
| Audit logging | Yes | Yes | `backend/tests/test_rbac_and_tenancy.py`, `test_auth.py` |
| Enterprise connectors | No | No | — |
| Observability | No | No | — |
| Incident investigation | No | No | — |
| Docker deployment | Yes | Yes (CI builds all images and checks health) | `.github/workflows/ci.yml` (docker job) |
