import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { tokenStore, type Me, type Role } from "../api/client";
import { App } from "../App";

export function jsonResponse(body: unknown, status = 200) {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "x-request-id": "test-id" },
  });
}

type Handler = Response | Error | ((init?: RequestInit) => Response);
export type Routes = Record<string, Handler>;

/** Mock fetch with routes keyed by "METHOD /path" (path relative to /api/v1). */
export function mockApi(routes: Routes) {
  const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const key = `${(init?.method ?? "GET").toUpperCase()} ${path}`;
    const handler = routes[key];
    if (!handler) return jsonResponse({ detail: `no mock for ${key}` }, 404);
    if (handler instanceof Error) throw handler;
    if (typeof handler === "function") return handler(init);
    return handler.clone();
  });
  return spy;
}

export function makeMe(role: Role = "org_admin"): Me {
  return {
    user: {
      id: "u-1",
      organization_id: "o-1",
      email: "asha@acme.com",
      full_name: "Asha Admin",
      role,
      is_active: true,
      last_login_at: null,
      created_at: "2026-11-24T10:00:00Z",
    },
    organization: {
      id: "o-1",
      name: "Acme Corp",
      slug: "acme-corp",
      is_active: true,
      created_at: "2026-11-24T10:00:00Z",
    },
  };
}

/** Routes for an already signed-in user (the refresh cookie resumes the session). */
export function signedInRoutes(role: Role = "org_admin"): Routes {
  return {
    "POST /auth/refresh": jsonResponse({ access_token: "token-1", token_type: "bearer", expires_in: 900 }),
    "GET /auth/me": jsonResponse(makeMe(role)),
    "GET /health/live": jsonResponse({ status: "ok" }),
    "GET /health/ready": jsonResponse({
      status: "ok",
      checks: { database: { status: "ok", latency_ms: 1.5, error: null } },
    }),
    "GET /system/info": jsonResponse({ name: "NexusAI", version: "0.6.0", environment: "local", answer_provider: "extractive", chat_model: null }),
  };
}

export function renderApp(path = "/") {
  tokenStore.set(null);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
