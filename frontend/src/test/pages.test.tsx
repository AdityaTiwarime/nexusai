import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import type { User } from "../api/client";
import { jsonResponse, makeMe, mockApi, renderApp, signedInRoutes } from "./helpers";

const employee: User = {
  ...makeMe("employee").user,
  id: "u-2",
  email: "ravi@acme.com",
  full_name: "Ravi Kumar",
};

describe("Dashboard", () => {
  it("shows healthy status when the API and database are up", async () => {
    mockApi(signedInRoutes());
    renderApp("/");
    expect(await screen.findByText("Online")).toBeInTheDocument();
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(await screen.findByText("Healthy")).toBeInTheDocument();
    expect(screen.getByText("1.5 ms")).toBeInTheDocument();
  });

  it("shows the database as down when readiness returns 503", async () => {
    mockApi({
      ...signedInRoutes(),
      "GET /health/ready": jsonResponse(
        {
          status: "error",
          checks: { database: { status: "error", latency_ms: null, error: "ConnectionRefusedError" } },
        },
        503,
      ),
    });
    renderApp("/");
    expect(await screen.findByText("Degraded")).toBeInTheDocument();
    expect(await screen.findByText("ConnectionRefusedError")).toBeInTheDocument();
  });
});

describe("Dashboard optional dependencies", () => {
  it("marks Redis as optional and keeps the platform ready when it is down", async () => {
    mockApi({
      ...signedInRoutes(),
      "GET /health/ready": jsonResponse({
        status: "ok",
        checks: {
          database: { status: "ok", critical: true, latency_ms: 1.5, error: null },
          redis: { status: "error", critical: false, latency_ms: null, error: "ConnectionError" },
        },
      }),
    });
    renderApp("/");
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(await screen.findByText("optional")).toBeInTheDocument();
    expect(screen.getByText("ConnectionError")).toBeInTheDocument();
    expect(screen.getByText("Down")).toBeInTheDocument();
  });
});

describe("Role-based navigation", () => {
  it("shows Users and Audit Logs to an org admin", async () => {
    mockApi(signedInRoutes("org_admin"));
    renderApp("/");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Users" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Audit Logs" })).toBeInTheDocument();
  });

  it("hides admin pages from an employee and redirects direct visits", async () => {
    mockApi(signedInRoutes("employee"));
    renderApp("/audit-logs");
    expect(await screen.findByText("System overview")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Audit Logs" })).not.toBeInTheDocument();
  });
});

describe("Users page", () => {
  it("lets an org admin add a user", async () => {
    let created: unknown = null;
    mockApi({
      ...signedInRoutes("org_admin"),
      "GET /users": jsonResponse([makeMe().user]),
      "POST /users": (init) => {
        created = JSON.parse(String(init?.body));
        return jsonResponse(employee, 201);
      },
    });
    renderApp("/users");

    fireEvent.change(await screen.findByLabelText("Full name"), { target: { value: "Ravi Kumar" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ravi@acme.com" } });
    fireEvent.change(screen.getByLabelText(/Temporary password/), { target: { value: "Long-Enough-Pass-1" } });
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "manager" } });
    fireEvent.click(screen.getByRole("button", { name: "Add user" }));

    await waitFor(() =>
      expect(created).toEqual({
        full_name: "Ravi Kumar",
        email: "ravi@acme.com",
        password: "Long-Enough-Pass-1",
        role: "manager",
      }),
    );
    expect(await screen.findByText("User added.")).toBeInTheDocument();
  });

  it("does not let an admin edit their own role", async () => {
    mockApi({ ...signedInRoutes("org_admin"), "GET /users": jsonResponse([makeMe().user, employee]) });
    renderApp("/users");
    expect(await screen.findByLabelText("Role for ravi@acme.com")).toBeInTheDocument();
    expect(screen.queryByLabelText("Role for asha@acme.com")).not.toBeInTheDocument();
  });

  it("is read-only for a manager", async () => {
    mockApi({ ...signedInRoutes("manager"), "GET /users": jsonResponse([makeMe().user, employee]) });
    renderApp("/users");
    expect(await screen.findByText("ravi@acme.com")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add user" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Role for ravi@acme.com")).not.toBeInTheDocument();
  });
});

describe("Audit logs page", () => {
  it("lists events with readable change details", async () => {
    mockApi({
      ...signedInRoutes("org_admin"),
      "GET /audit-logs": jsonResponse({
        items: [
          {
            id: "a-1",
            actor_id: "u-1",
            actor_email: "asha@acme.com",
            action: "user.updated",
            target_type: "user",
            target_id: "u-2",
            ip_address: "127.0.0.1",
            request_id: "abc123def456789",
            details: { role: { from: "employee", to: "manager" } },
            created_at: "2026-11-24T10:00:00Z",
          },
        ],
        total: 1,
        limit: 25,
        offset: 0,
      }),
    });
    renderApp("/audit-logs");
    // Wait for the table row ("user.updated" alone also matches the filter dropdown option).
    expect(await screen.findByText("role: employee → manager")).toBeInTheDocument();
    expect(screen.getByText("user.updated", { selector: "code" })).toBeInTheDocument();
    expect(screen.getByText("asha@acme.com")).toBeInTheDocument();
    expect(screen.getByText("abc123def456")).toBeInTheDocument();
  });
});
