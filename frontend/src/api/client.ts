// Typed API client. Every call goes through `request`, so error handling and
// (from Phase 2) auth headers live in one place.

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
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { Accept: "application/json", ...init?.headers },
  });
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

export type CheckStatus = "ok" | "error";

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
};
