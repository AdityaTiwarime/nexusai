import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { App } from "../App";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "x-request-id": "test-id" },
  });
}

function mockApi(routes: Record<string, Response | Error>) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    const match = Object.keys(routes).find((path) => url.endsWith(path));
    const result = match ? routes[match] : jsonResponse({ detail: "not found" }, 404);
    if (result instanceof Error) throw result;
    return result.clone();
  });
}

function renderApp(path = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Dashboard", () => {
  it("shows healthy status when the API and database are up", async () => {
    mockApi({
      "/health/live": jsonResponse({ status: "ok" }),
      "/health/ready": jsonResponse({
        status: "ok",
        checks: { database: { status: "ok", latency_ms: 1.5, error: null } },
      }),
      "/system/info": jsonResponse({ name: "NexusAI", version: "0.1.0", environment: "local" }),
    });

    renderApp();

    expect(await screen.findByText("Online")).toBeInTheDocument();
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(await screen.findByText("Healthy")).toBeInTheDocument();
    expect(screen.getByText("1.5 ms")).toBeInTheDocument();
    expect(await screen.findByText(/v0\.1\.0/)).toBeInTheDocument();
  });

  it("shows the database as down when readiness returns 503", async () => {
    mockApi({
      "/health/live": jsonResponse({ status: "ok" }),
      "/health/ready": jsonResponse(
        {
          status: "error",
          checks: { database: { status: "error", latency_ms: null, error: "ConnectionRefusedError" } },
        },
        503,
      ),
      "/system/info": jsonResponse({ name: "NexusAI", version: "0.1.0", environment: "local" }),
    });

    renderApp();

    expect(await screen.findByText("Degraded")).toBeInTheDocument();
    expect(await screen.findByText("Down")).toBeInTheDocument();
    expect(screen.getByText("ConnectionRefusedError")).toBeInTheDocument();
  });

  it("explains when the API cannot be reached", async () => {
    mockApi({
      "/health/live": new TypeError("Failed to fetch"),
      "/health/ready": new TypeError("Failed to fetch"),
      "/system/info": new TypeError("Failed to fetch"),
    });

    renderApp();

    expect(await screen.findByText("Unreachable")).toBeInTheDocument();
    expect(await screen.findByText(/Is the backend running\?/)).toBeInTheDocument();
  });

  it("renders a 404 page for unknown routes", async () => {
    mockApi({});
    renderApp("/does-not-exist");
    expect(await screen.findByText("Page not found")).toBeInTheDocument();
  });
});
