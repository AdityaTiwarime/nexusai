# Security

This document describes how NexusAI authenticates users, authorizes actions and isolates tenants, which threats it addresses, and what is **not** covered yet. Everything listed as a control is implemented and tested; tests are referenced in [`testing.md`](testing.md).

## Authentication

| Aspect | Implementation | Why |
|---|---|---|
| Password storage | **Argon2id** (`argon2-cffi`) | Memory-hard; the current OWASP recommendation. Plain passwords are never stored or logged. |
| Password policy | 12–128 characters; must not contain the user's email name | Length beats complexity rules. The upper bound limits hashing cost from huge inputs. |
| Login errors | One generic message: *"Invalid email or password"* | Doesn't reveal which emails have accounts. |
| Timing | Unknown emails are still checked against a dummy hash | "Unknown email" and "wrong password" take the same time. |
| Access token | JWT (HS256), **15 minutes**, sent as `Authorization: Bearer` | Short-lived, so a leaked token is useful only briefly. |
| JWT validation | Algorithm pinned to HS256; `exp`, `iat`, `sub`, `iss` required; `type=access` | Blocks `alg: none` tokens, algorithm confusion, expired tokens and tokens from other issuers. |
| Refresh token | 384-bit random value in an **httpOnly, SameSite=Strict** cookie scoped to `/api/v1/auth`; only its **SHA-256 hash** is stored | JavaScript can't read it, so XSS can't steal it, and a database leak doesn't expose usable tokens. |
| Rotation | A new refresh token is issued on every refresh; the old one is revoked | A stolen token stops working as soon as the real user refreshes. |
| Reuse detection | Presenting an already-rotated token revokes the **whole token family** and writes an audit event | Detects token theft and ends the attacker's session as well as the user's. |
| Logout | Revokes the token family and clears the cookie | The session can't be resumed afterwards. |
| Access token storage (frontend) | In memory only, never `localStorage` | Not readable from storage by injected scripts. |

## Authorization (RBAC)

Roles are fixed and enforced on the **server** for every request through `require_roles(...)`. Hiding buttons in the UI is a convenience, not a control.

| Capability | Employee | Manager | Org admin | Super admin |
|---|:-:|:-:|:-:|:-:|
| View own profile / organization | ✅ | ✅ | ✅ | profile only |
| List users in own organization | ❌ | ✅ | ✅ | ❌ |
| Create users, change roles, (de)activate | ❌ | ❌ | ✅ | ❌ |
| Read audit logs of own organization | ❌ | ❌ | ✅ | ❌ |
| List all organizations (platform view) | ❌ | ❌ | ❌ | ✅ |

Additional rules:

- **The current role is re-read from the database on every request.** Promotions, demotions and deactivations take effect immediately, not when the token expires.
- Deactivating a user also **revokes all their refresh tokens**.
- An admin **cannot change their own role or deactivate themselves** (prevents locking an organization out).
- The `super_admin` role **cannot be assigned through the API**. It is created only with the CLI (`python -m app.cli create-superadmin`), which reads the password from an environment variable or a prompt so it never lands in shell history.

## Tenant isolation

Every organization is a tenant. The rules:

1. **The tenant comes from the authenticated user, never from the request.** Endpoints filter by the caller's `organization_id` as stored in the database. An `organization_id` sent in a request body is ignored.
2. **Cross-tenant IDs behave like non-existent IDs (404).** Asking for another organization's user returns *not found*, so the API doesn't even confirm that the ID exists.
3. **The super admin is not a tenant member.** It belongs to no organization (enforced by a database `CHECK` constraint) and has no access to tenant data endpoints.
4. **Database constraints back the application rules**: `CHECK` constraints on valid roles and on "super admin ⇔ no organization", foreign keys with `ON DELETE CASCADE` from organizations, and tenant-first composite indexes.

## Audit logging

Security-relevant events are written to `audit_logs` **in the same transaction** as the action they describe, so the log never claims something that didn't happen:

`organization.registered`, `auth.login`, `auth.login_failed` (with a reason that only admins can see), `auth.logout`, `auth.refresh_token_reuse_detected`, `user.created`, `user.updated` (with before/after values).

Each entry stores the actor, IP address and **request ID**, which matches the `X-Request-ID` response header and the server log lines for that request. Audit logs are only readable by org admins, and only for their own organization.

## Rate limiting

Backed by Redis, so limits hold across several API replicas.

| Limit | Default | Setting |
|---|---|---|
| Login attempts per IP | 20 per minute | `NEXUS_LOGIN_RATE_LIMIT_PER_IP` |
| **Failed** logins per account | 5 per 15 minutes; after that even the right password gets 429 until the window ends | `NEXUS_LOGIN_FAILURES_PER_ACCOUNT` |
| Sign-ups per IP | 10 per hour | `NEXUS_REGISTER_RATE_LIMIT_PER_IP` |

Responses are `429` with `Retry-After`. IPs and emails are stored in Redis only as SHA-256 hashes. If Redis is unreachable the limiter **fails open** (requests allowed, warning logged): availability of login is preferred, and Argon2id still makes guessing slow. Trade-off: the per-account limit lets an attacker temporarily lock a known email out; the window is short (15 minutes) and this is the standard compromise.

Behind the bundled nginx, the API trusts `X-Forwarded-For` (`FORWARDED_ALLOW_IPS` in `docker-compose.yml`) so limits apply to real client IPs. That's only safe while the backend port isn't exposed to the internet directly.

## Configuration safety

Startup **refuses to run in staging or production** if:

- `NEXUS_JWT_SECRET` is the development default or shorter than 32 characters, or
- `NEXUS_COOKIE_SECURE` is not `true` (the refresh cookie must only travel over HTTPS).

API docs (`/docs`, `/openapi.json`) are disabled in production. Error responses never include stack traces, connection strings or internal messages.

## Threat model (current scope)

| Threat | Mitigation |
|---|---|
| Credential stuffing / password guessing | Argon2id hashing, generic errors, failed-login audit trail, and **rate limiting** (see below). |
| Stolen access token | 15-minute lifetime; role and active status re-checked on every request. |
| Stolen refresh token | httpOnly cookie, rotation, reuse detection with family revocation. |
| Forged or tampered JWT | Signature check with pinned algorithm; `alg: none` and foreign keys rejected (tested). |
| Cross-tenant data access (IDOR) | Tenant scoping on every query, 404 for foreign IDs, tenant ignored in request bodies (tested). |
| Privilege escalation | Server-side role checks; `super_admin` not assignable via API; self-demotion blocked (tested). |
| CSRF | Refresh cookie is `SameSite=Strict` and path-scoped; all other endpoints require a Bearer header, which browsers never attach automatically. |
| Account enumeration | Identical response and timing for unknown email vs. wrong password; 404 for foreign IDs. |
| Leaking internals | Generic 500 responses with request ID only; readiness check exposes only the error type. |
| Malicious uploads | Extension allow-list **and** content checks (magic bytes, DOCX structure, text decoding), size limit, DOCX zip-bomb guard. Files are stored under server-generated keys, never user file names (no path traversal). Downloads are always `application/octet-stream` + `Content-Disposition: attachment` + `nosniff`, so an uploaded file is never rendered by the browser. |
| AI answers leaking restricted content | Retrieval filters by organization, role and status **inside the SQL query**, so restricted passages never reach the answer step (tested with an employee asking about an admin-only document). Old citations are re-checked on every read and hidden once a source is restricted or deleted. Conversations are private to their owner. |
| Prompt injection through documents | Sources are wrapped in delimited blocks, attempts to close a block are neutralised, the system prompt treats sources as data, and output is validated in code (only real citations; an ungrounded answer becomes "no evidence"). The chat has no tools or actions, so an injection can at worst distort one answer. Not a complete defence; see `ai-design.md`. |
| An AI agent doing something harmful | Agents can only use 3 registered tools with strict schemas (no SQL, shell or arbitrary HTTP). Anything that creates or sends needs a human approval of the exact actions. Plans from any planner are validated and rejected as a whole if they use an unknown tool or extra arguments (tested with a model proposing `DROP TABLE`). |
| Approving your own risky request | Separation of duties: the requester can never approve or reject their own run, whatever their role; employees can't approve at all. |
| Double execution (double click, two approvers) | Conditional `UPDATE … WHERE status = 'pending'` decision, plus unique (run, step) constraints on every created record (tested concurrently). |
| SSRF through a configured integration URL | Base URLs must be http(s), without credentials, on a host in `NEXUS_INTEGRATION_ALLOWED_HOSTS`; redirects aren't followed (tested with cloud metadata IPs, internal hosts, look-alike domains). |
| Leaked integration credentials | Encrypted at rest (Fernet) with a key outside the database; never returned by the API; audit logs record which fields changed, never values; production refuses the dev key. |
| Forged or replayed webhooks | HMAC-SHA256 over timestamp + raw body, constant-time compare, 5-minute window, unique event ids, 64 KB limit, per-integration rotatable secret (tested). |
| Duplicate side effects in external systems | Idempotency keys per (run, step) on every external create; 4xx errors aren't retried. |
| Abuse of the AI endpoint (cost) | Per-user rate limit on questions and searches; the model isn't called when nothing relevant was retrieved. |
| Seeing restricted documents | Per-document `min_role`, applied in the SQL query; hidden documents return 404 like missing ones; duplicates of hidden documents don't reveal their ID (tested). |

## Known limitations (planned)

| Limitation | Plan |
|---|---|
| No malware scanning of uploads | Add a ClamAV (or cloud) scan step in the worker before parsing (Phase 10). |
| No email verification or password reset | Planned for Phase 10 (the email connector exists now, for agent emails). |
| No MFA or SSO (OAuth2/OIDC) | Planned for Phase 10. |
| Admin sets the first password for new users | Replace with invite links once email is available. |
| HS256 shared secret | Sufficient for a single service; move to RS256 with key rotation if other services need to verify tokens. |
