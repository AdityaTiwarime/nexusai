import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import type { AgentRun, Approval } from "../api/client";
import { jsonResponse, makeMe, mockApi, renderApp, signedInRoutes } from "./helpers";

const pendingRun: AgentRun = {
  id: "r-1",
  request: "Customer jane@globex.com can't log in. Open a high priority ticket and email her.",
  status: "awaiting_approval",
  planner: "rules",
  steps: [
    {
      index: 0,
      tool: "create_ticket",
      args: { title: "Customer can't log in", description: "…", priority: "high", customer_email: "jane@globex.com" },
      reason: "Track the request as a high priority ticket.",
      mutating: true,
      status: "awaiting_approval",
      result: null,
      error: null,
    },
    {
      index: 1,
      tool: "notify_customer",
      args: { to: "jane@globex.com", subject: "Update on your request (ticket {{ticket_number}})", body: "Hello" },
      reason: "Let jane@globex.com know the request is being handled.",
      mutating: true,
      status: "awaiting_approval",
      result: null,
      error: null,
    },
  ],
  summary: "2 actions waiting for approval.",
  error: null,
  requested_by: "u-2",
  requested_by_email: "ravi@acme.com",
  created_at: "2026-11-24T10:00:00Z",
  updated_at: "2026-11-24T10:00:00Z",
  completed_at: null,
  approval: {
    id: "a-1",
    status: "pending",
    expires_at: new Date(Date.now() + 5 * 3_600_000).toISOString(),
    decided_by_email: null,
    decided_at: null,
    comment: null,
  },
};

const completedRun: AgentRun = {
  ...pendingRun,
  status: "completed",
  steps: [
    { ...pendingRun.steps[0], status: "done", result: { ticket_number: "TCK-1001", priority: "high", verified: true } },
    {
      ...pendingRun.steps[1],
      status: "done",
      result: { to: "jane@globex.com", status: "recorded", note: "Recorded in the outbox; not sent.", verified: true },
    },
  ],
  summary: "Created ticket TCK-1001 (high priority). Recorded an email to jane@globex.com in the outbox (not sent).",
  approval: { ...pendingRun.approval!, status: "approved", decided_by_email: "asha@acme.com", comment: "OK" },
};

const approval: Approval = {
  id: "a-1",
  agent_run_id: "r-1",
  status: "pending",
  request: pendingRun.request,
  actions: pendingRun.steps.map(({ index, tool, args, reason }) => ({ index, tool, args, reason })),
  requested_by_email: "ravi@acme.com",
  expires_at: pendingRun.approval!.expires_at,
  decided_by_email: null,
  decided_at: null,
  comment: null,
  created_at: "2026-11-24T10:00:00Z",
};

describe("Agent workflows", () => {
  it("plans a task and shows the steps waiting for approval", async () => {
    let sent: unknown = null;
    mockApi({
      ...signedInRoutes("employee"),
      "GET /agents/runs": jsonResponse([]),
      "POST /agents/runs": (init) => {
        sent = JSON.parse(String(init?.body));
        return jsonResponse(pendingRun, 201);
      },
      "GET /agents/runs/r-1": jsonResponse(pendingRun),
    });
    renderApp("/workflows");
    fireEvent.click(await screen.findByRole("button", { name: /Customer jane@globex.com can't log in/ }));
    fireEvent.click(screen.getByRole("button", { name: "Plan and run" }));

    expect(await screen.findByText(/Waiting for a manager or admin \(not you\) to approve/)).toBeInTheDocument();
    expect(sent).toEqual({
      request: "Customer jane@globex.com can't log in to the portal. Open a high priority ticket and email her.",
    });
    expect(screen.getByText("Create ticket")).toBeInTheDocument();
    expect(screen.getByText("Email customer")).toBeInTheDocument();
    expect(screen.getAllByText("needs approval")).toHaveLength(2);
    expect(screen.getByText(/h \d+ min left/)).toBeInTheDocument();
    // The exact arguments are visible, including the placeholder filled at execution time.
    expect(screen.getByText("Update on your request (ticket {{ticket_number}})")).toBeInTheDocument();
  });

  it("shows results, the approver and the comment of a completed run", async () => {
    mockApi({
      ...signedInRoutes("employee"),
      "GET /agents/runs": jsonResponse([completedRun]),
      "GET /agents/runs/r-1": jsonResponse(completedRun),
    });
    renderApp("/workflows/r-1");
    expect(await screen.findByText("TCK-1001")).toBeInTheDocument();
    expect(screen.getAllByText("✓ verified")).toHaveLength(2);
    expect(screen.getByText(/by asha@acme.com/)).toBeInTheDocument();
    expect(screen.getByText("“OK”")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry failed steps" })).not.toBeInTheDocument();
  });

  it("lets a manager retry a failed approved run", async () => {
    const failed: AgentRun = {
      ...completedRun,
      status: "failed",
      error: "notify_customer failed: RuntimeError",
      steps: [completedRun.steps[0], { ...pendingRun.steps[1], status: "failed", error: "notify_customer failed: RuntimeError" }],
    };
    let retried = false;
    mockApi({
      ...signedInRoutes("manager"),
      "GET /agents/runs": jsonResponse([failed]),
      "GET /agents/runs/r-1": jsonResponse(failed),
      "POST /agents/runs/r-1/retry": () => {
        retried = true;
        return jsonResponse(completedRun);
      },
    });
    renderApp("/workflows/r-1");
    fireEvent.click(await screen.findByRole("button", { name: "Retry failed steps" }));
    await waitFor(() => expect(retried).toBe(true));
    expect(await screen.findByText("Completed", { selector: "span" })).toBeInTheDocument();
  });

  it("shows planning failures with guidance", async () => {
    mockApi({
      ...signedInRoutes("employee"),
      "GET /agents/runs": jsonResponse([]),
      "GET /agents/runs/r-2": jsonResponse({
        ...pendingRun,
        id: "r-2",
        status: "failed",
        steps: [],
        approval: null,
        summary: null,
        error: "I couldn't turn this into actions. Try something like: …",
      }),
    });
    renderApp("/workflows/r-2");
    expect(await screen.findByRole("alert")).toHaveTextContent("couldn't turn this into actions");
  });
});

describe("Approvals", () => {
  it("shows exactly what will run and approves with a comment", async () => {
    let decision: { url: string; body: unknown } | null = null;
    mockApi({
      ...signedInRoutes("manager"),
      "GET /approvals": jsonResponse([approval]),
      "POST /approvals/a-1/approve": (init) => {
        decision = { url: "approve", body: JSON.parse(String(init?.body)) };
        return jsonResponse(completedRun);
      },
    });
    renderApp("/approvals");
    expect(await screen.findByText("If approved, exactly these actions will run")).toBeInTheDocument();
    const card = screen.getByRole("article");
    expect(within(card).getByText("1. Create ticket")).toBeInTheDocument();
    expect(within(card).getByText("2. Email customer")).toBeInTheDocument();
    expect(within(card).getAllByText("jane@globex.com").length).toBeGreaterThan(0);

    fireEvent.change(within(card).getByLabelText("Comment for request by ravi@acme.com"), { target: { value: "Go ahead" } });
    fireEvent.click(within(card).getByRole("button", { name: "Approve and run" }));
    await waitFor(() => expect(decision).toEqual({ url: "approve", body: { comment: "Go ahead" } }));
  });

  it("does not offer decisions on your own request", async () => {
    mockApi({
      ...signedInRoutes("manager"),
      "GET /approvals": jsonResponse([{ ...approval, requested_by_email: makeMe("manager").user.email }]),
    });
    renderApp("/approvals");
    expect(await screen.findByText(/This is your own request/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve and run" })).not.toBeInTheDocument();
  });

  it("shows the server's reason when a decision is refused", async () => {
    mockApi({
      ...signedInRoutes("org_admin"),
      "GET /approvals": jsonResponse([approval]),
      "POST /approvals/a-1/reject": jsonResponse({ detail: "This request is already approved." }, 409),
    });
    renderApp("/approvals");
    fireEvent.click(await screen.findByRole("button", { name: "Reject" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already approved");
  });

  it("is not available to employees", async () => {
    mockApi(signedInRoutes("employee"));
    renderApp("/approvals");
    expect(await screen.findByText("System overview")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).queryByRole("link", { name: "Approvals" })).not.toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Agent Workflows" })).toBeInTheDocument();
  });
});
