# Testing

## Strategy

| Layer | Tool | Runs against |
|---|---|---|
| Backend unit + integration | pytest, pytest-asyncio, httpx | A **real PostgreSQL** database (no SQLite stand-in), schema created by the real Alembic migrations, tables emptied after every test |
| Migrations | pytest + Alembic CLI | Real PostgreSQL: upgrade → downgrade → upgrade, plus `alembic check` (models match migrations) |
| Frontend | Vitest + Testing Library (jsdom) | Mocked `fetch` responses |
| Full stack | Docker Compose in CI | db + backend + frontend containers, health-checked through nginx |
| Browser end-to-end | Playwright (run manually, see below) | Live backend + frontend + PostgreSQL |

Later phases add ingestion and reliability tests (Phase 3+), automated Playwright end-to-end tests in CI and Locust load tests (Phase 10).

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

## Results (actual runs)

Latest run: 24 Nov 2026, Linux, Python 3.13, Node 22, PostgreSQL 16. GitHub Actions runs the same suites (plus the Docker stack check) on every push.

### Backend: 59 passed

| File | Tests | What it proves |
|---|---|---|
| `test_auth.py` | 22 | Registration creates an org admin and sets an **httpOnly, SameSite=Strict, path-scoped** refresh cookie; duplicate emails rejected (case-insensitive); duplicate org names get unique slugs; weak passwords rejected; public sign-up can be disabled; login is case-insensitive; **wrong password and unknown email return identical responses**; failed logins are audited; requests without a token, with a **tampered** token, a token **signed with another key**, an **expired** token or an **`alg: none`** token are all rejected; refresh **rotates** the token; **replaying an old refresh token revokes the whole session** and is audited; logout revokes the session; production refuses the dev JWT secret and insecure cookies |
| `test_rbac_and_tenancy.py` | 16 | **Tenant isolation**: user lists only show your organization; another org's user by ID returns **404** for both read and update; audit logs are org-scoped; an `organization_id` in the request body is ignored. **RBAC**: employee/manager/org-admin permissions; role changes and deactivation take effect **immediately on existing tokens**; admins can't demote or disable themselves; `super_admin` can't be assigned via the API; user changes are audited with before/after values and request ID; the super admin sees all orgs but no tenant data; org admins can't use platform endpoints |
| `test_config.py` | 7 | Non-async DB drivers rejected; settings load from plain env vars exactly as Docker passes them; production disables API docs |
| `test_health.py` | 5 | Liveness/readiness, 503 on DB outage without leaking credentials or hosts |
| `test_middleware.py` | 6 | Request IDs, safe 500 responses, CORS allow/deny |
| `test_migrations.py` | 3 | Upgrade/downgrade/re-upgrade, a single head, and models match migrations |

Lint (`ruff check`, `ruff format --check`) clean. `pip-audit`: no known vulnerabilities.

### Frontend: 14 passed

Plus `tsc` type check, production build, and `npm audit` (production dependencies) clean.

| File | Tests | What it proves |
|---|---|---|
| `auth.test.tsx` | 6 | Signed-out visitors are sent to login; login shows the dashboard and sends the token as a Bearer header; server error messages are shown; a session resumes from the refresh cookie; an expired access token is **refreshed once and the request retried**; sign-out |
| `pages.test.tsx` | 8 | Dashboard health states; **role-based navigation** (admin sees Users and Audit Logs, an employee doesn't and is redirected from direct visits); admins can add users and can't edit their own role; managers get a read-only user list; audit log entries render with readable change details |

### Browser end-to-end (Playwright against live servers)

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
