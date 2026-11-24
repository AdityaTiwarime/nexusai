import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, ApiError } from "../api/client";
import { Button, ErrorAlert, inputClass } from "../components/ui";

const PAGE_SIZE = 25;
const ACTIONS = [
  "",
  "auth.login",
  "auth.login_failed",
  "auth.logout",
  "auth.refresh_token_reuse_detected",
  "organization.registered",
  "user.created",
  "user.updated",
  "document.uploaded",
  "document.version_added",
  "document.reprocess_requested",
  "document.updated",
  "document.deleted",
];

function describe(details: Record<string, unknown>): string {
  const entries = Object.entries(details);
  if (entries.length === 0) return "—";
  return entries
    .map(([key, value]) => {
      if (value && typeof value === "object" && "from" in value && "to" in value) {
        const change = value as { from: unknown; to: unknown };
        return `${key}: ${String(change.from)} → ${String(change.to)}`;
      }
      return `${key}: ${String(value)}`;
    })
    .join(", ");
}

export function AuditLogsPage() {
  const [action, setAction] = useState("");
  const [page, setPage] = useState(0);
  const logs = useQuery({
    queryKey: ["audit-logs", action, page],
    queryFn: () => api.auditLogs({ limit: PAGE_SIZE, offset: page * PAGE_SIZE, action: action || undefined }),
    placeholderData: keepPreviousData,
  });
  const total = logs.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Audit logs</h1>
          <p className="mt-1 text-sm text-slate-500">
            Security-relevant activity in your organization. Each entry carries the request ID for tracing.
          </p>
        </div>
        <label className="text-sm">
          <span className="mr-2 text-slate-600">Action</span>
          <select
            aria-label="Filter by action"
            className={`${inputClass} inline-block w-64`}
            value={action}
            onChange={(e) => {
              setAction(e.target.value);
              setPage(0);
            }}
          >
            {ACTIONS.map((a) => (
              <option key={a} value={a}>{a || "All actions"}</option>
            ))}
          </select>
        </label>
      </header>

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        {logs.isPending ? (
          <p className="text-sm text-slate-500">Loading…</p>
        ) : logs.isError ? (
          <ErrorAlert message={logs.error instanceof ApiError ? logs.error.detail : "Failed to load"} />
        ) : logs.data.items.length === 0 ? (
          <p className="text-sm text-slate-500">No events yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="pb-2 pr-4 font-medium">Time</th>
                  <th className="pb-2 pr-4 font-medium">Action</th>
                  <th className="pb-2 pr-4 font-medium">Actor</th>
                  <th className="pb-2 pr-4 font-medium">Details</th>
                  <th className="pb-2 font-medium">Request ID</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {logs.data.items.map((log) => (
                  <tr key={log.id}>
                    <td className="py-2.5 pr-4 text-xs text-slate-500 whitespace-nowrap">
                      {new Date(log.created_at).toLocaleString()}
                    </td>
                    <td className="py-2.5 pr-4">
                      <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">{log.action}</code>
                    </td>
                    <td className="py-2.5 pr-4 text-slate-700">{log.actor_email ?? "—"}</td>
                    <td className="py-2.5 pr-4 text-slate-600">{describe(log.details)}</td>
                    <td className="py-2.5 font-mono text-xs text-slate-400">{log.request_id?.slice(0, 12) ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="mt-4 flex items-center justify-between text-sm text-slate-500">
          <span>
            {total} event{total === 1 ? "" : "s"}
          </span>
          <div className="flex items-center gap-2">
            <Button variant="secondary" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
              Previous
            </Button>
            <span>
              Page {page + 1} of {pages}
            </span>
            <Button variant="secondary" disabled={page + 1 >= pages} onClick={() => setPage((p) => p + 1)}>
              Next
            </Button>
          </div>
        </div>
      </section>
    </div>
  );
}
