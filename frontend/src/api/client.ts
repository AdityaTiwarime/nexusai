// Typed API client.
//
// Auth model:
// - The access token lives only in memory (this module), never in localStorage,
//   so a script injected into the page can't read it from storage.
// - The refresh token is an httpOnly cookie the browser sends to /api/v1/auth/*.
// - On a 401, the client refreshes once (sharing one in-flight refresh between
//   parallel requests) and retries; if that fails the user is signed out.

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly requestId: string | null,
    public readonly body: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** A human-readable message from FastAPI's error body. */
  get detail(): string {
    const body = this.body as { detail?: unknown } | null;
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string; loc?: unknown[] };
      const field = Array.isArray(first.loc) ? String(first.loc[first.loc.length - 1]) : "";
      const msg = (first.msg ?? "Invalid input").replace(/^Value error, /, "");
      return field && field !== "body" ? `${field}: ${msg}` : msg;
    }
    return this.message;
  }
}

let accessToken: string | null = null;
let refreshInFlight: Promise<boolean> | null = null;
let onSessionExpired: (() => void) | null = null;

export const tokenStore = {
  get: () => accessToken,
  set: (token: string | null) => {
    accessToken = token;
  },
  onExpired: (handler: (() => void) | null) => {
    onSessionExpired = handler;
  },
};

async function rawRequest(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  return fetch(`${API_BASE}${path}`, { ...init, headers, credentials: "same-origin" });
}

async function parse<T>(path: string, response: Response): Promise<T> {
  if (response.status === 204) return undefined as T;
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(
      `Request to ${path} failed with ${response.status}`,
      response.status,
      response.headers.get("x-request-id"),
      body,
    );
  }
  return body as T;
}

/** Exchange the refresh cookie for a new access token. Returns false if signed out. */
export async function refreshSession(): Promise<boolean> {
  refreshInFlight ??= (async () => {
    try {
      const response = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        accessToken = null;
        return false;
      }
      const body = (await response.json()) as TokenResponse;
      accessToken = body.access_token;
      return true;
    } catch {
      accessToken = null;
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response = await rawRequest(path, init);
  if (response.status === 401 && accessToken && !path.startsWith("/auth/")) {
    if (await refreshSession()) {
      response = await rawRequest(path, init);
    } else {
      onSessionExpired?.();
    }
  }
  return parse<T>(path, response);
}

// --- types -----------------------------------------------------------------------

export type CheckStatus = "ok" | "error";
export type Role = "super_admin" | "org_admin" | "manager" | "employee";

export interface CheckResult {
  status: CheckStatus;
  latency_ms: number | null;
  error: string | null;
}

export interface ReadyResponse {
  status: CheckStatus;
  checks: Record<string, CheckResult>;
}

export interface SystemInfo {
  name: string;
  version: string;
  environment: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface User {
  id: string;
  organization_id: string | null;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  is_active: boolean;
  created_at: string;
}

export interface Me {
  user: User;
  organization: Organization | null;
}

export interface AuditLog {
  id: string;
  actor_id: string | null;
  actor_email: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  ip_address: string | null;
  request_id: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface AuditLogPage {
  items: AuditLog[];
  total: number;
  limit: number;
  offset: number;
}

export interface RegisterInput {
  organization_name: string;
  full_name: string;
  email: string;
  password: string;
}

export interface NewUserInput {
  email: string;
  full_name: string;
  password: string;
  role: Exclude<Role, "super_admin">;
}

export interface UserUpdateInput {
  full_name?: string;
  role?: Exclude<Role, "super_admin">;
  is_active?: boolean;
}

// --- endpoints ---------------------------------------------------------------------

const json = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  live: () => request<{ status: "ok" }>("/health/live"),
  // A 503 from /health/ready still carries the per-check details we want to show.
  ready: async (): Promise<ReadyResponse> => {
    try {
      return await request<ReadyResponse>("/health/ready");
    } catch (err) {
      if (err instanceof ApiError && err.status === 503 && err.body) {
        return err.body as ReadyResponse;
      }
      throw err;
    }
  },
  systemInfo: () => request<SystemInfo>("/system/info"),

  login: (email: string, password: string) =>
    request<TokenResponse>("/auth/login", json({ email, password })),
  register: (input: RegisterInput) => request<TokenResponse>("/auth/register", json(input)),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  me: () => request<Me>("/auth/me"),

  listUsers: () => request<User[]>("/users"),
  createUser: (input: NewUserInput) => request<User>("/users", json(input)),
  updateUser: (id: string, input: UserUpdateInput) =>
    request<User>(`/users/${id}`, { method: "PATCH", body: JSON.stringify(input) }),

  auditLogs: (params: { limit?: number; offset?: number; action?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.limit) query.set("limit", String(params.limit));
    if (params.offset) query.set("offset", String(params.offset));
    if (params.action) query.set("action", params.action);
    const qs = query.toString();
    return request<AuditLogPage>(`/audit-logs${qs ? `?${qs}` : ""}`);
  },
};
