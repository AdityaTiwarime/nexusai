# NexusAI Architecture

## High-level view

```mermaid
flowchart TB
    subgraph Client
        UI[React + TypeScript SPA<br/>Vite · Tailwind · TanStack Query]
    end

    subgraph API["FastAPI application (modular monolith)"]
        GW[HTTP layer<br/>CORS · request IDs · rate limits]
        AUTH[Auth · RBAC · tenant isolation]
        RAG[Retrieval & RAG engine]
        AGENT[Agent engine<br/>LangGraph state machine]
        INT[Integration service<br/>connector framework]
        WF[Workflow & approval service]
        AUD[Audit & analytics]
        INC[Incident investigation]
    end

    subgraph Workers["Background worker (same codebase)"]
        ING[Document ingestion<br/>parse · chunk · embed]
        RUN[Long-running agent runs]
    end

    subgraph Data
        PG[(PostgreSQL + pgvector)]
        REDIS[(Redis<br/>queues · cache · rate limits)]
        KAFKA[(Kafka<br/>domain event log)]
    end

    subgraph External
        LLM[LLM providers<br/>via common interface]
        CRM[Salesforce / ServiceNow / Graph<br/>mock connectors first]
    end

    subgraph Observability
        OTEL[OpenTelemetry collector]
        PROM[Prometheus]
        GRAF[Grafana]
    end

    UI -->|REST / JSON| GW
    GW --> AUTH
    AUTH --> RAG & AGENT & INT & WF & AUD & INC
    RAG --> PG
    RAG --> LLM
    AGENT --> RAG
    AGENT --> WF
    AGENT --> INT
    INT --> CRM
    WF --> PG
    AUD --> PG
    API --> REDIS
    REDIS --> Workers
    Workers --> PG
    Workers --> LLM
    API -. events .-> KAFKA
    KAFKA -. events .-> Workers
    API -. traces/metrics .-> OTEL
    Workers -. traces/metrics .-> OTEL
    OTEL --> PROM --> GRAF
```

## Component responsibilities

| Component | Responsibility | Phase |
|---|---|---|
| Frontend | Dashboard, chat workspace, documents, approvals, admin, observability pages | 1 → 10 |
| HTTP layer | Routing, CORS, request/correlation IDs, error envelope, rate limiting | 1 (IDs), 2 (rate limits) |
| Auth / RBAC / tenancy | Users, orgs, roles, JWT access + refresh tokens, tenant-scoped queries | 2 |
| Document intelligence | Upload validation, text + metadata extraction, chunking, embeddings, versioning, dedupe | 3 |
| RAG engine | Query preprocessing, hybrid retrieval, permission filtering, reranking, cited answers, "insufficient evidence" responses | 4 |
| Agent engine | LangGraph state machine: intent → plan → tool selection → approval gate → execution → validation | 5 |
| Workflow & approvals | Pending / approved / rejected / expired approvals, ownership, history | 5 |
| Integration service | Connector interface, OAuth2/token storage, health checks, retries, rate-limit handling, webhooks | 6 |
| Event layer | Event schemas, validation, idempotent consumers, retries, dead-letter queue | 7 |
| Audit & analytics | Append-only audit log of security-relevant and AI actions | 2+ |
| Observability | OTel traces and metrics (app, AI, infra), Grafana dashboards | 8 |
| Incident investigation | Gathers traces/logs/metrics, builds a timeline, separates evidence from AI hypotheses, retrieves runbooks | 9 |

## Request flow (Phase 1)

```mermaid
sequenceDiagram
    participant B as Browser (React)
    participant N as nginx (frontend container)
    participant A as FastAPI
    participant D as PostgreSQL

    B->>N: GET /api/v1/health/ready
    N->>A: proxy /api/* (keeps same origin)
    A->>A: assign X-Request-ID, start timer
    A->>D: SELECT 1 (with timeout)
    D-->>A: ok
    A-->>N: 200 {status: ok, checks: {database: ok}}
    N-->>B: 200 + X-Request-ID header
```

## Key design decisions

1. **Modular monolith first.** One FastAPI app with clear module boundaries is faster to build, test and deploy than ten microservices, and it is what most real enterprise AI products start as. The worker is a separate *process* from the same codebase so slow work never blocks API requests.
2. **Async SQLAlchemy 2.0 + asyncpg.** The API spends most of its time waiting on the database and LLM providers; async I/O keeps one process responsive under concurrent requests.
3. **Liveness vs readiness.** `/health/live` never touches dependencies (is the process up?). `/health/ready` checks PostgreSQL with a timeout (can it serve traffic?). Orchestrators like ECS/Kubernetes use these differently: a failing readiness check stops traffic, a failing liveness check restarts the container.
4. **Request IDs from day one.** Every response carries `X-Request-ID`, and every log line includes it. Phase 8 links the same ID to OpenTelemetry trace IDs, so a request can be followed across the API, RAG pipeline, agents and connectors.
5. **Configuration through environment variables** (pydantic-settings), validated at startup. Secrets never live in the repo; `.env.example` documents every variable.
6. **Same-origin API in Docker.** nginx serves the built frontend and proxies `/api` to the backend, so the browser never needs CORS in the containerised setup. CORS is still configured for the Vite dev server.
7. **Provider-agnostic LLM layer (Phase 4).** Application code depends on an interface, not on one vendor's SDK; a deterministic fake provider keeps tests and demos runnable without API keys.
