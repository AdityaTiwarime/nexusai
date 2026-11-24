import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiError, type Approval, type ApprovalStatus } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { ArgsTable, StatusChip, TOOL_LABELS, timeLeft } from "../components/agents";
import { Button, ErrorAlert } from "../components/ui";

const TABS: { label: string; status: ApprovalStatus }[] = [
  { label: "Pending", status: "pending" },
  { label: "Approved", status: "approved" },
  { label: "Rejected", status: "rejected" },
  { label: "Expired", status: "expired" },
];

function errorText(err: unknown): string | null {
  if (!err) return null;
  return err instanceof ApiError ? err.detail : "Something went wrong";
}

function ApprovalCard({ approval }: { approval: Approval }) {
  const { me } = useAuth();
  const queryClient = useQueryClient();
  const [comment, setComment] = useState("");
  const decide = useMutation({
    mutationFn: (decision: "approve" | "reject") => api.decide(approval.id, decision, comment.trim() || undefined),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
      queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
      queryClient.invalidateQueries({ queryKey: ["agent-run", approval.agent_run_id] });
    },
  });
  const own = approval.requested_by_email === me?.user.email;
  const pending = approval.status === "pending";

  return (
    <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-slate-900">{approval.request}</p>
          <p className="mt-1 text-xs text-slate-500">
            Requested by {approval.requested_by_email ?? "a deleted user"} ·{" "}
            {new Date(approval.created_at).toLocaleString()}
            {pending && ` · ${timeLeft(approval.expires_at)}`} ·{" "}
            <Link to={`/workflows/${approval.agent_run_id}`} className="text-brand-700 hover:underline">
              view run
            </Link>
          </p>
        </div>
        <StatusChip status={approval.status} />
      </div>

      <p className="mt-4 text-xs font-medium uppercase tracking-wide text-slate-500">
        {pending ? "If approved, exactly these actions will run" : "Actions"}
      </p>
      <ol className="mt-2 space-y-2">
        {approval.actions.map((action) => (
          <li key={action.index} className="rounded-lg bg-slate-50 p-3 ring-1 ring-inset ring-slate-200">
            <p className="text-sm font-medium text-slate-900">
              {action.index + 1}. {TOOL_LABELS[action.tool] ?? action.tool}
              <span className="ml-2 font-normal text-slate-500">{action.reason}</span>
            </p>
            <div className="mt-2">
              <ArgsTable args={action.args} />
            </div>
          </li>
        ))}
      </ol>

      {!pending && (
        <p className="mt-3 text-sm text-slate-600">
          {approval.decided_by_email && `Decided by ${approval.decided_by_email}`}
          {approval.decided_at && ` · ${new Date(approval.decided_at).toLocaleString()}`}
          {approval.comment && <span className="block pt-1 italic">“{approval.comment}”</span>}
        </p>
      )}

      {pending && (
        <div className="mt-4 space-y-2">
          {own ? (
            <p className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-600">
              This is your own request. Another manager or admin has to decide on it.
            </p>
          ) : (
            <>
              <textarea
                aria-label={`Comment for request by ${approval.requested_by_email}`}
                rows={2}
                maxLength={1000}
                placeholder="Optional comment (recorded in the audit log)"
                className="block w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                value={comment}
                onChange={(e) => setComment(e.target.value)}
              />
              <div className="flex gap-2">
                <Button disabled={decide.isPending} onClick={() => decide.mutate("approve")}>
                  {decide.isPending && decide.variables === "approve" ? "Approving…" : "Approve and run"}
                </Button>
                <Button variant="secondary" disabled={decide.isPending} onClick={() => decide.mutate("reject")}>
                  Reject
                </Button>
              </div>
            </>
          )}
          <ErrorAlert message={errorText(decide.error)} />
        </div>
      )}
    </article>
  );
}

export function ApprovalsPage() {
  const [tab, setTab] = useState(0);
  const approvals = useQuery({
    queryKey: ["approvals", TABS[tab].status],
    queryFn: () => api.listApprovals(TABS[tab].status),
    placeholderData: keepPreviousData,
  });

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Approvals</h1>
        <p className="mt-1 text-sm text-slate-500">
          Actions proposed by AI agents wait here for a human decision. You can't decide on your own requests, and
          requests expire after 24 hours.
        </p>
      </header>

      <div role="tablist" aria-label="Approval status" className="flex gap-1">
        {TABS.map((t, i) => (
          <button
            key={t.status}
            role="tab"
            aria-selected={tab === i}
            onClick={() => setTab(i)}
            className={`rounded-md px-3 py-1.5 text-sm font-medium ${
              tab === i ? "bg-brand-50 text-brand-700" : "text-slate-600 hover:bg-slate-100"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {approvals.isPending ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : approvals.isError ? (
        <ErrorAlert message={errorText(approvals.error)} />
      ) : approvals.data.length === 0 ? (
        <p className="py-8 text-center text-sm text-slate-500">Nothing here.</p>
      ) : (
        <div className="space-y-4">
          {approvals.data.map((approval) => (
            <ApprovalCard key={approval.id} approval={approval} />
          ))}
        </div>
      )}
    </div>
  );
}
