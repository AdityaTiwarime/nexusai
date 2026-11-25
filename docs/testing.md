# Testing

## Strategy

| Layer | Tool | Runs against |
|---|---|---|
| Backend unit + integration | pytest, pytest-asyncio, httpx | A **real PostgreSQL** database (no SQLite stand-in), schema created by the real Alembic migrations, tables emptied after every test |
| Migrations | pytest + Alembic CLI | Real PostgreSQL: upgrade → downgrade → upgrade, plus `alembic check` (models match migrations) |
| Frontend | Vitest + Testing Library (jsdom) | Mocked `fetch` responses |
| Worker reliability | pytest | Real PostgreSQL: retries, backoff, crash recovery, concurrent workers, superseded jobs |
| Rate limiting | pytest | A **real Redis** (`NEXUS_TEST_REDIS_URL`), plus an unreachable one for fail-open |
| Full stack | Docker Compose in CI | db + redis + backend + worker + frontend, health-checked, plus `scripts/smoke_test.py` (register → upload → worker → chunks through nginx) |
| Browser end-to-end | Playwright (run manually, see below) | Live backend + frontend + PostgreSQL |
| Terraform | `tofu test` with **mocked AWS providers**, `tofu validate`, Checkov | No AWS account needed; security rules asserted on the planned resources |
| Deploy and backup scripts | pytest with fake `docker`, `curl` and `aws` | `deploy/tests`; plus the real scripts in the CI `production-stack` job |
| Production stack | GitHub Actions | Caddy HTTPS stack started by `deploy.sh`, smoke test over HTTPS, forced rollback, Trivy, OWASP ZAP |
| Kubernetes | kubeconform + Checkov (rendered manifests); **kind** in CI | Real cluster: rollout, smoke test, network-policy enforcement probes, rollback |

Still to come: automated Playwright tests in CI and Locust load tests.

## Commands

```bash
# Backend (needs PostgreSQL and Redis; `docker compose up -d db redis` gives you both)
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
export NEXUS_TEST_DATABASE_URL=postgresql+asyncpg://nexus:nexus@localhost:5432/nexusai_test
export NEXUS_TEST_REDIS_URL=redis://localhost:6379/15   # db 15 is flushed by the tests
pytest -v
ruff check . && ruff format --check .

# Deploy scripts (no Docker or AWS needed)
pip install pytest && pytest deploy/tests

# Terraform (OpenTofu or Terraform >= 1.6; no AWS credentials needed)
cd infra/terraform/aws && tofu init -backend=false && tofu validate && tofu test

# Kubernetes manifests
kubectl kustomize deploy/k8s/overlays/kind | kubeconform -strict -summary

# Frontend
cd frontend
npm ci
npm run typecheck
npm test
npm run build
```

## Results (actual runs)

Latest run: 25 Nov 2026, Linux, Python 3.13, Node 22, PostgreSQL 16. GitHub Actions runs the same suites (plus the Docker stack, production stack and Kubernetes checks) on every push.

### Backend: 249 passed

| File | Tests | What it proves |
|---|---|---|
| `test_auth.py` | 22 | Registration creates an org admin and sets an **httpOnly, SameSite=Strict, path-scoped** refresh cookie; duplicate emails rejected (case-insensitive); duplicate org names get unique slugs; weak passwords rejected; public sign-up can be disabled; login is case-insensitive; **wrong password and unknown email return identical responses**; failed logins are audited; requests without a token, with a **tampered** token, a token **signed with another key**, an **expired** token or an **`alg: none`** token are all rejected; refresh **rotates** the token; **replaying an old refresh token revokes the whole session** and is audited; logout revokes the session; production refuses the dev JWT secret and insecure cookies |
| `test_rbac_and_tenancy.py` | 16 | **Tenant isolation**: user lists only show your organization; another org's user by ID returns **404** for both read and update; audit logs are org-scoped; an `organization_id` in the request body is ignored. **RBAC**: employee/manager/org-admin permissions; role changes and deactivation take effect **immediately on existing tokens**; admins can't demote or disable themselves; `super_admin` can't be assigned via the API; user changes are audited with before/after values and request ID; the super admin sees all orgs but no tenant data; org admins can't use platform endpoints |
| `test_config.py` | 12 | Non-async DB drivers rejected; settings load from plain env vars exactly as Docker passes them; production disables API docs; **production refuses the dev encryption key and the default (or an empty) database password**; encryption key format; allowed hosts from env |
| `test_health.py` | 5 | Liveness/readiness, 503 on DB outage without leaking credentials or hosts; system info reports the answer and embedding providers |
| `test_middleware.py` | 7 | Request IDs, safe 500 responses, CORS allow/deny, **`Cache-Control: no-store` and `nosniff` on API responses** |
| `test_schema_check.py` | 7 | The Kubernetes init-container check: the schema counts as ready at head, **pending** before migrations or at an older revision (with a timeout), **accepted when newer** (rollback onto a backward-compatible schema), and an unreachable database counts as pending |
| `test_migrations.py` | 3 | Upgrade/downgrade/re-upgrade (including the pgvector, chat and agent tables), a single head, and models match migrations |
| `test_parsing_and_chunking.py` | 23 | Disallowed extensions, empty and oversized files, **content that doesn't match its extension**, non-Word zips and a **DOCX zip bomb** are rejected; PDF text keeps **page numbers** and metadata; image-only and corrupt PDFs fail with a clear reason; DOCX paragraphs and tables; CSV rows become self-describing lines; BOM and Windows-1252 text; chunks respect the size limit, **overlap** with the previous chunk, **lose no text**, hard-split very long lines, track page ranges and drop NUL bytes |
| `test_embeddings.py` | 12 | Hashing embedder is deterministic, unit-length and reflects word overlap; OpenAI provider (against a **mocked HTTP API**, no real calls) sends the right request, re-orders results, classifies 429/5xx/timeouts as retryable and 400/401/bad dimensions as permanent; provider chosen by configuration; the OpenAI provider requires a key |
| `test_documents.py` | 18 | Upload is queued, the worker makes it ready with 1,536-dim embeddings and a generated `tsvector`; PDF and DOCX end to end with page ranges; invalid files leave **nothing stored**; size limit; **duplicate detection** (and not across organizations); employees can read but not upload or delete; **`min_role` hides documents** from lower roles (404 on read, chunks and download); managers can't restrict above their own role; a duplicate of a hidden document doesn't leak its ID; **other organizations get 404 on every endpoint**; super admin has no document access; download returns the original bytes as an attachment with `nosniff`; new versions replace chunks and keep old files downloadable; versions must keep the file type; managers manage only their own uploads; delete removes rows and files and is audited; list filters, search (LIKE wildcards escaped), counts and paging |
| `test_ingestion_worker.py` | 13 | **Retryable errors retried** with exponential backoff, then succeed; documents fail after max attempts; permanent errors fail immediately; unparseable files fail with a reason and can be **reprocessed**; missing files fail permanently; a **crashed worker's job is reclaimed** after the lock timeout, and a job that keeps crashing workers is given up; **3 concurrent workers never claim the same job**; a job for an outdated version is **superseded**, as are queued jobs when a new version arrives; reprocessing is idempotent; worker heartbeat health check |
| `test_rag.py` | 20 | Hybrid search ranks the right passage first with a keyword match; **search never returns documents above the caller's role** and is **isolated between organizations**; documents are searchable only once ready; search can be limited to chosen documents; stop-word-only and unrelated queries return nothing; answers quote the documents with **citations** (document, snippet); **unanswerable questions return "no evidence"**, as does an empty knowledge base; **an employee gets "no evidence" for a question only an admin's document answers**; follow-ups stay in one conversation; **conversations are private** to their owner (404 for colleagues, even admins); **citations become unavailable when the source is restricted or deleted**; rename/delete; validation; per-user rate limit; super admin has no access |
| `test_answering.py` | 21 | Text normalisation; the reranker prefers passages covering the question; the extractive answerer quotes the best sentences in reading order, refuses weak matches and skips duplicates from overlapping chunks; **LLM answerer** (mocked API): invented citation numbers are removed, **ungrounded answers (no valid citation, or `INSUFFICIENT_EVIDENCE`) become "no evidence"**, the model isn't called without sources, errors are classified; through the API: **the system prompt and source blocks are sent as designed and an injected `</source>` is neutralised**, temperature 0, token usage recorded, conversation history sent on follow-ups, **provider failures return 503/502 without leaking details and save nothing** |
| `test_rag_eval.py` | 1 | The benchmark in `backend/evals/` (results in [`ai-design.md`](ai-design.md#evaluation)); fails if hit@3 drops below 90%, correct answers below 80%, any unanswerable question gets answered, or any paraphrased question gets a wrong answer |
| `test_agents.py` | 27 | Rule planner understands ticket/email/search requests (and looks up the customer first); **plans are validated against the tool registry** (unknown tools, invalid or extra arguments such as `"sql": "DROP TABLE users"`, step limit); tools are listed with their approval requirement; read-only runs complete immediately; **nothing is created before approval**; approved actions execute exactly as shown, with `{{ticket_number}}` filled in, records verified, created on behalf of the requester and fully audited; **nobody can approve their own request**; employees can't see or decide approvals; **a decision can only be made once**; **two simultaneous approvals execute once**; rejection runs nothing; **expired requests can't be approved**; run visibility (own vs. all); other organizations see nothing; ticket numbers are sequential per organization; **a failing action's partial write is rolled back and retry doesn't duplicate the ticket**; unclear requests fail with guidance; LLM planner (mocked API): JSON mode, validated tools, rule-breaking plans rejected, provider outage returns 503 and saves nothing |
| `test_integrations.py` | 20 | Against the **in-process mock services** (real HTTP over ASGI): connecting ServiceNow (health check, webhook registered, secret shown once, **password never returned and stored encrypted**); invalid connections rejected **before anything is contacted** (cloud metadata IP, internal host, wrong scheme, credentials in URL, missing/extra secret fields); one per kind, admin only; wrong credentials show as failing (401 not retried) and can be fixed, with **no secret values in the audit log**; other organizations get 404; **a full agent run creates a ServiceNow incident (urgency, caller, correlation id), looks up the Salesforce contact and sends the email with the incident number**; **flaky 503s are retried (3 attempts) without a duplicate incident**; a rate-limited system fails the step, logs 3 attempts, and **retry later creates exactly one incident**; same idempotency key returns the same incident; disabled or missing connections fall back to internal records; an expired Salesforce token is refreshed; **resolving the incident updates the ticket through a signed webhook**; webhook verification (duplicate, wrong secret, **replayed timestamp, tampered body**, missing headers, oversized, malformed, unknown); secret rotation; disabled integrations reject webhooks |
| `test_connectors.py` | 16 | Host allowlist accepts listed hosts and subdomains and rejects look-alikes, internal addresses and `file://`; **timeouts are retried with exponential backoff and jitter**, then reported; **`Retry-After` is honoured but capped**; 4xx errors aren't retried; auth and idempotency headers are sent; redirects aren't followed; Graph-style email payload; encryption round trip and wrong-key rejection |
| `test_rate_limit.py` | 6 | Repeated failed logins **lock the account temporarily** (even the right password gets 429 with `Retry-After`), other accounts unaffected; successful logins don't count; per-IP login and sign-up limits; **Redis down → requests still allowed** and readiness stays ok with Redis reported as a non-critical error |

Lint (`ruff check`, `ruff format --check`) clean. `pip-audit`: no known vulnerabilities.

### Frontend: 45 passed

Plus `tsc` type check, production build, and `npm audit` (production dependencies) clean.

| File | Tests | What it proves |
|---|---|---|
| `auth.test.tsx` | 6 | Signed-out visitors are sent to login; login shows the dashboard and sends the token as a Bearer header; server error messages are shown; a session resumes from the refresh cookie; an expired access token is **refreshed once and the request retried**; sign-out |
| `pages.test.tsx` | 9 | Dashboard health states; **role-based navigation** (admin sees Users and Audit Logs, an employee doesn't and is redirected from direct visits); admins can add users and can't edit their own role; managers get a read-only user list; audit log entries render with readable change details; Redis shown as an optional dependency |
| `documents.test.tsx` | 9 | Document list with status, visibility, failure reasons and status counts; upload sends **multipart form data** with title and visibility (managers can't pick "admins only"); rejected uploads show the API's reason and link to the duplicate; employees get no upload form; detail page shows facts, versions and chunks with page numbers; uploader can reprocess a failed document; delete needs confirmation; hidden/foreign documents show "not found"; a `?chunk=` link opens the right page of chunks and highlights the target |
| `agents.test.tsx` | 8 | Starting a task from an example shows the planned steps, "needs approval" badges, exact arguments and time left; completed runs show results, verification, approver and comment; managers can retry failed approved runs; planning failures show guidance; the Approvals page shows exactly what will run and sends approve with a comment; **no decision buttons on your own request**; the server's refusal reason is shown; employees don't get the Approvals page |
| `integrations.test.tsx` | 6 | Connection health, masked credentials, webhook URL and the call log (attempts, errors); connecting with the bundled mock values sends the right payload and shows the webhook secret once; the server's refusal (e.g. host not allowed) is shown; admins can test a failing connection; read-only for managers; agent runs link the ServiceNow incident and show the Salesforce customer |
| `chat.test.tsx` | 7 | AI Chat: asking (Enter to send) shows the cited answer, **both the [1] marker and the source card link to the exact chunk**, offline-mode label and latency; follow-ups send the conversation ID; "no evidence" answers are clearly marked; **restricted sources show as unavailable without links**; provider errors show the API's message. Search: ranked passages with keyword badge, score, highlights and chunk links; empty results message |


### Phase 10: deployment and security checks

| Check | Result |
|---|---|
| `tofu test` (OpenTofu 1.9.1, mocked AWS 5.80 and random providers) | **9 passed**: only 80/443 open, outbound 443 only, empty default SG, IMDSv2 with hop limit 1, encrypted disks, no public IP except the EIP, **no secret in user data**, Fernet key conversion, no wildcard IAM actions, one readable secret, write-only backups, private TLS-only KMS bucket, no deploy role without a repo, deploy role scoped to one repo environment and one instance, invalid image tag and domain rejected. A test was confirmed to fail when the hop limit was changed to 2 |
| `tofu validate`, `tofu fmt -check` | clean |
| Checkov, Terraform | 106 passed, 0 failed, 7 skipped (each with a written reason) |
| Checkov, rendered Kubernetes manifests | 937 passed, 0 failed, 1 skipped (+5 check IDs skipped in CI with reasons) |
| kubeconform `-strict`, Kubernetes 1.31 | kind overlay 28/28 valid, production overlay 19/19 valid |
| shellcheck | `deploy.sh`, `backup.sh`, the network-policy probe, the **rendered EC2 boot script** and the embedded `nexusai-deploy` command: clean |
| actionlint | both workflows clean |
| `deploy/tests` | **9 passed**: healthy deploy remembered; health checked through the real domain via Caddy; **unhealthy release rolled back**; failed pull rolled back without restarting; first release with nothing to roll back to; unhealthy rollback exits 2; backup streams the dump and uploads to S3; **a failed dump fails the backup and deletes the partial object** |
| `nexusai-deploy` (rendered) | rejects `abc; rm -rf /` with exit 64 |
| Docker Compose production file | `docker compose config` valid; refuses to start without the required secrets |

**Local HTTPS run.** The production Caddyfile was run with the real Caddy 2.8.4 binary (`NEXUS_DOMAIN=localhost`) in front of the frontend's nginx configuration (nginx 1.24) and the live API, worker and mock services:

- `http://localhost/` → **308** to `https://localhost/`. The HTTPS response had HSTS, the full CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, COOP and **no `Server` header**. API responses had `Cache-Control: no-store`.
- `scripts/smoke_test.py https://localhost` passed **end to end over HTTPS**: upload → worker → cited answer → refusal → approved agent run → ServiceNow/Salesforce/email → signed webhook resolved the ticket.
- Playwright signed in and opened every page under the strict CSP with **0 CSP violations and 0 page errors**.

Not run locally (no Docker daemon or kind in this environment): the production containers themselves, Trivy, ZAP and the kind cluster. Those run in GitHub Actions (`production-stack` and `kubernetes` jobs).

**First CI run:** `infra`, `kubernetes` (including the network-policy enforcement probes) and the production-stack deploy, HTTPS smoke test and forced rollback all passed. The **Trivy scan failed, as designed**, on fixable CRITICAL vulnerabilities: OpenSSL and Brotli in the frontend's Alpine base image, and urllib3 in the backend image. Fix: both images now apply their distribution's security updates at build time (`apk upgrade`, `apt-get upgrade`), the frontend moved to the stable nginx 1.28 line, and pip (with its vendored libraries) is removed from the backend image after installing dependencies.

### Phase 6 end-to-end (live API + worker + Redis + PostgreSQL + mock services)

- `scripts/smoke_test.py` (also in CI against the Docker stack, including the `mock-services` container): connected ServiceNow, Salesforce and email (health checks passed), then an approved agent run produced `Customer: Jane Cooper (Globex Corporation, Enterprise). Created ticket TCK-1002 (ServiceNow INC0010001) (high priority). Sent an email to jane@globex.com.` Resolving `INC0010001` in the mock ServiceNow delivered a signed webhook (HTTP 200), and the ticket became `resolved`.
- Playwright: an admin connected all three systems from the Integrations page with "Fill in the bundled mock service". With ServiceNow set to fail twice (`flaky`), **Test connection succeeded after 3 attempts (shown in the call log)**. An agent run was approved by a manager in a second browser, and the run page showed the Salesforce customer, a link to `INC0010002` and "Sent an email … through the email connection". The resolve webhook was delivered to both organizations' registered URLs (a mock shortcut) with HTTP 200 each. No browser errors.

Screenshots: `docs/screenshots/integrations.png`, `workflow-integrations.png`.

### Phase 5 end-to-end (live API + worker + Redis + PostgreSQL)

- `scripts/smoke_test.py` (also run in CI against the Docker stack): an agent run waited for approval, **the requester's own approval was refused (403)**, a newly added manager approved it, and the run completed: `Created ticket TCK-1001 (high priority). Recorded an email to jane@example.com in the outbox (not sent).`
- Playwright with three separate browser sessions: the org admin added an employee and a manager. The employee (who has no Approvals link) asked the agent to open a ticket and email a customer, and saw it waiting for approval. The manager saw the exact actions, approved with a comment, and the queue emptied. The employee then saw `TCK-1001` and both actions verified. The admin's own request showed "This is your own request" with no decision buttons, and the audit log showed the agent events. No browser errors.

Screenshots: `docs/screenshots/workflow-pending.png`, `approvals.png`, `workflow-completed.png`.

### Phase 4 end-to-end (live API + worker + Redis + PostgreSQL)

- `scripts/smoke_test.py` (also run in CI against the Docker stack): upload → worker → chunks, then `Is train travel preferred for short trips?` was **answered with a citation to the uploaded document**, and `Who is the CEO of the company?` was **refused** ("no evidence").
- Playwright in Chromium: uploaded the 5 benchmark documents through the UI, asked about paid leave (answered: "24 days … [1]", source: employee-handbook), asked a follow-up about password rotation in the same conversation (answered: "180 days [1]"), asked about the enterprise refund policy (**"No supporting evidence"**). Clicking citation [1] opened the handbook with the cited chunk **highlighted**. Searching "laptop lost or stolen" ranked the IT security policy first, showing the "Lost or stolen laptops…" excerpt with highlights. The conversation was still listed after a reload. No browser errors.
- This run found and fixed a UI race: picking the next file while an upload was still in progress cleared the new selection when the first upload finished.

Screenshots: `docs/screenshots/chat.png`, `search.png`.

### Phase 3 end-to-end (live API + worker + Redis + PostgreSQL)

- `scripts/smoke_test.py` against a running API and worker: registered an org, uploaded a Markdown file, the worker processed it into chunks (`ok: worker processed the document into 1 chunk(s)`). The same script runs in CI against the Docker stack.
- `/health/ready` reported database and Redis healthy; `python -m app.worker --healthcheck` passed.
- Playwright in Chromium through the Vite dev server: registered "Globex Industries", uploaded a 3-page PDF handbook (**Ready**, chunk shows "Pages 1–3"), a Markdown file restricted to managers (**Ready**), and a fake PDF (**Failed**: "The PDF is corrupt or unreadable"); re-uploading the handbook showed **"This file has already been uploaded"**; uploading a new version moved the document to **v2** and the chunks showed the new text. No browser errors.

Screenshots: `docs/screenshots/documents.png`, `document-detail.png`, `dashboard.png`.

### Phase 2 browser end-to-end (Playwright against live servers)

Run against uvicorn, the Vite dev server and PostgreSQL 16 with a fresh database:

1. Visiting `/users` while signed out redirected to the login page.
2. Registered "Acme Corp" through the UI and landed on the dashboard.
3. **Reloaded the page and stayed signed in** (session resumed from the httpOnly refresh cookie).
4. As org admin, added a manager. Earlier, a temporary password containing the user's name was **rejected with a clear error**, as designed.
5. The audit log page showed `organization.registered` and `user.created` with actor, details and request ID.
6. Signed out, then reloaded: **stayed signed out** (refresh token revoked server-side).
7. Signed in as the manager: **Users** visible, **Audit Logs** hidden; calling `/api/v1/audit-logs` without a token returned **401**.

Screenshots: `docs/screenshots/`.

### Earlier verification (Phase 1, still valid)

- Stopping PostgreSQL turns `/health/ready` into 503 while `/health/live` stays 200; restarting it restores 200 **without restarting the API**.
- The Docker Compose stack builds and passes health checks in CI.
