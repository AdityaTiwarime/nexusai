import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import type { AgentRun, Integration } from "../api/client";
import { jsonResponse, mockApi, renderApp, signedInRoutes } from "./helpers";

const servicenow: Integration = {
  id: "i-1",
  kind: "servicenow",
  label: "ServiceNow",
  name: "ServiceNow (prod)",
  base_url: "https://acme.service-now.com",
  config: { webhook_registered: true },
  secret_fields: ["password", "username"],
  status: "active",
  health_status: "ok",
  health_checked_at: "2026-11-24T10:00:00Z",
  last_error: null,
  webhook_url: "https://nexus.example.com/api/v1/webhooks/servicenow/i-1",
  created_at: "2026-11-24T10:00:00Z",
};

const calls = [
  { id: "c-1", operation: "create_incident", outcome: "success", http_status: 201, attempts: 3, latency_ms: 412, error: null, created_at: "2026-11-24T10:05:00Z" },
  { id: "c-2", operation: "health_check", outcome: "failure", http_status: 429, attempts: 3, latency_ms: 90, error: "returned HTTP 429", created_at: "2026-11-24T10:04:00Z" },
];

describe("Integrations page", () => {
  it("shows connection health, masked credentials, webhook URL and the call log", async () => {
    mockApi({
      ...signedInRoutes("org_admin"),
      "GET /integrations": jsonResponse([servicenow]),
      "GET /integrations/i-1/calls": jsonResponse(calls),
    });
    renderApp("/integrations");
    const card = await screen.findByRole("region", { name: "ServiceNow" });
    expect(await within(card).findByText("Connected")).toBeInTheDocument();
    expect(within(card).getByText("password: ••••••")).toBeInTheDocument();
    expect(within(card).getByText(servicenow.webhook_url!)).toBeInTheDocument();
    expect(await within(card).findByText("create_incident")).toBeInTheDocument();
    expect(within(card).getByText("returned HTTP 429")).toBeInTheDocument();
    // Not connected yet: an admin gets the connect form.
    const salesforce = screen.getByRole("region", { name: "Salesforce" });
    expect(within(salesforce).getByRole("button", { name: "Connect Salesforce" })).toBeInTheDocument();
  });

  it("connects a system with the demo values and shows the webhook secret once", async () => {
    let sent: unknown = null;
    mockApi({
      ...signedInRoutes("org_admin"),
      "GET /integrations": jsonResponse([]),
      "POST /integrations": (init) => {
        sent = JSON.parse(String(init?.body));
        return jsonResponse({ ...servicenow, webhook_secret: "s3cr3t-shown-once" }, 201);
      },
    });
    renderApp("/integrations");
    const card = await screen.findByRole("region", { name: "ServiceNow" });
    fireEvent.click(within(card).getByRole("button", { name: "Fill in the bundled mock service" }));
    fireEvent.click(within(card).getByRole("button", { name: "Connect ServiceNow" }));
    expect(await screen.findByText("s3cr3t-shown-once")).toBeInTheDocument();
    expect(sent).toEqual({
      kind: "servicenow",
      name: "ServiceNow (demo)",
      base_url: "http://mock-services:9000",
      config: {},
      secrets: { username: "nexus", password: "servicenow-demo-password" },
    });
  });

  it("shows why a connection was refused", async () => {
    mockApi({
      ...signedInRoutes("org_admin"),
      "GET /integrations": jsonResponse([]),
      "POST /integrations": jsonResponse({ detail: "Host '169.254.169.254' is not allowed." }, 422),
    });
    renderApp("/integrations");
    const card = await screen.findByRole("region", { name: "Email" });
    fireEvent.change(within(card).getByLabelText(/Email base URL/), { target: { value: "http://169.254.169.254" } });
    fireEvent.change(within(card).getByLabelText("Sender address"), { target: { value: "a@b.com" } });
    fireEvent.change(within(card).getByLabelText(/API key/), { target: { value: "k" } });
    fireEvent.click(within(card).getByRole("button", { name: "Connect Email" }));
    expect(await within(card).findByRole("alert")).toHaveTextContent("is not allowed");
  });

  it("lets an admin test a failing connection", async () => {
    let tested = false;
    mockApi({
      ...signedInRoutes("org_admin"),
      "GET /integrations": jsonResponse([{ ...servicenow, health_status: "error", last_error: "servicenow health_check failed: rejected the request (HTTP 401)" }]),
      "GET /integrations/i-1/calls": jsonResponse([]),
      "POST /integrations/i-1/test": () => {
        tested = true;
        return jsonResponse(servicenow);
      },
    });
    renderApp("/integrations");
    expect(await screen.findByText("Failing")).toBeInTheDocument();
    expect(screen.getByText(/HTTP 401/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await waitFor(() => expect(tested).toBe(true));
  });

  it("is read-only for managers", async () => {
    mockApi({
      ...signedInRoutes("manager"),
      "GET /integrations": jsonResponse([servicenow]),
      "GET /integrations/i-1/calls": jsonResponse([]),
    });
    renderApp("/integrations");
    expect(await screen.findByText("Connected")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Test connection" })).not.toBeInTheDocument();
    expect(screen.getAllByText(/An org admin can connect/)).toHaveLength(2);
  });
});

describe("Agent runs with connected systems", () => {
  it("links the ServiceNow incident and shows the Salesforce customer", async () => {
    const run: AgentRun = {
      id: "r-1",
      request: "Customer jane@globex.com can't log in. Open a high priority ticket and email her.",
      status: "completed",
      planner: "rules",
      steps: [
        { index: 0, tool: "lookup_customer", args: { email: "jane@globex.com" }, reason: "", mutating: false, status: "done", error: null,
          result: { found: true, name: "Jane Cooper", account: "Globex Corporation", tier: "Enterprise", owner: "Arjun Mehta" } },
        { index: 1, tool: "create_ticket", args: { title: "x" }, reason: "", mutating: true, status: "done", error: null,
          result: { ticket_number: "TCK-1001", priority: "high", external_number: "INC0010001", external_url: "https://acme.service-now.com/x", verified: true } },
        { index: 2, tool: "notify_customer", args: { to: "jane@globex.com" }, reason: "", mutating: true, status: "done", error: null,
          result: { to: "jane@globex.com", status: "sent", verified: true } },
      ],
      summary: "Created ticket TCK-1001 (ServiceNow INC0010001).",
      error: null,
      requested_by: "u-1",
      requested_by_email: "asha@acme.com",
      created_at: "2026-11-24T10:00:00Z",
      updated_at: "2026-11-24T10:00:00Z",
      completed_at: "2026-11-24T10:01:00Z",
      approval: null,
    };
    mockApi({
      ...signedInRoutes("employee"),
      "GET /agents/runs": jsonResponse([run]),
      "GET /agents/runs/r-1": jsonResponse(run),
    });
    renderApp("/workflows/r-1");
    expect(await screen.findByText("Look up customer")).toBeInTheDocument();
    expect(screen.getByText("Jane Cooper")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "INC0010001" })).toHaveAttribute("href", "https://acme.service-now.com/x");
    expect(screen.getByText(/through the email connection/)).toBeInTheDocument();
  });
});
