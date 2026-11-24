import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, ApiError, type AgentRun } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { StatusChip, StepCard, timeLeft } from "../components/agents";
import { Button, ErrorAlert } from "../components/ui";

const EXAMPLES = [
  "Customer jane@globex.com can't log in to the portal. Open a high priority ticket and email her.",
  "Log an issue about the broken printer on floor 3, low priority",
  "What is the travel policy for short trips?",
];

function errorText(err: unknown): string | null {
  if (!err) return null;
  return err instanceof ApiError ? err.detail : "Something went wrong";
}

function NewRun() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [text, setText] = useState("");
  const start = useMutation({
    mutationFn: api.startRun,
    onSuccess: (run) => {
      setText("");
      queryClient.setQueryData(["agent-run", run.id], run);
      queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
      navigate(`/workflows/${run.id}`);
    },
  });

  return (
    <form
      className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) start.mutate(text.trim());
      }}
    >
      <h2 className="text-base font-semibold">New task</h2>
      <p className="mt-1 text-sm text-slate-500">
        Describe what you need. The agent plans the steps, runs read-only ones right away, and asks a manager to approve
        anything that creates or sends something.
      </p>
      <textarea
        aria-label="What should the agent do?"
        rows={3}
        maxLength={2000}
        className="mt-3 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
        placeholder="e.g. Customer jane@globex.com can't log in. Open a high priority ticket and email her."
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="mt-2 flex flex-wrap gap-2">
        {EXAMPLES.map((example) => (
          <button
            key={example}
            type="button"
            onClick={() => setText(example)}
            className="max-w-full truncate rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-600 hover:bg-slate-200"
          >
            {example}
          </button>
        ))}
      </div>
      <div className="mt-3 flex items-center gap-3">
        <Button type="submit" disabled={!text.trim() || start.isPending}>
          {start.isPending ? "Planning…" : "Plan and run"}
        </Button>
      </div>
      <div className="mt-3">
        <ErrorAlert message={errorText(start.error)} />
      </div>
    </form>
  );
}

function RunDetail({ id }: { id: string }) {
  const { hasRole } = useAuth();
  const queryClient = useQueryClient();
  const run = useQuery({ queryKey: ["agent-run", id], queryFn: () => api.getRun(id) });
  const retry = useMutation({
    mutationFn: () => api.retryRun(id),
    onSuccess: (updated) => {
      queryClient.setQueryData(["agent-run", id], updated);
      queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
    },
  });

  if (run.isPending) return <p className="text-sm text-slate-500">Loading…</p>;
  if (run.isError) {
    const notFound = run.error instanceof ApiError && run.error.status === 404;
    return <ErrorAlert message={notFound ? "Run not found, or you don't have access to it." : errorText(run.error)} />;
  }
  const data: AgentRun = run.data;
  const approval = data.approval;

  return (
    <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Request</p>
          <p className="mt-1 text-slate-900">{data.request}</p>
          <p className="mt-1 text-xs text-slate-500">
            {data.requested_by_email ?? "Deleted user"} · {new Date(data.created_at).toLocaleString()} · planner:{" "}
            {data.planner}
          </p>
        </div>
        <StatusChip status={data.status} />
      </div>

      {data.error && data.status === "failed" && <ErrorAlert message={data.error} />}

      {approval && approval.status === "pending" && (
        <div className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900 ring-1 ring-inset ring-amber-600/20">
          Waiting for a manager or admin (not you) to approve the actions marked “needs approval”.{" "}
          <span className="text-amber-700">{timeLeft(approval.expires_at)}.</span>
        </div>
      )}
      {approval && approval.status !== "pending" && (
        <p className="text-sm text-slate-600">
          <StatusChip status={approval.status} />{" "}
          {approval.decided_by_email && `by ${approval.decided_by_email}`}
          {approval.decided_at && ` · ${new Date(approval.decided_at).toLocaleString()}`}
          {approval.comment && <span className="block pt-1 italic text-slate-500">“{approval.comment}”</span>}
        </p>
      )}

      {data.summary && (
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Summary</p>
          <p className="mt-1 text-sm text-slate-800">{data.summary}</p>
        </div>
      )}

      {data.steps.length > 0 && (
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Plan</p>
          <ol className="mt-2 space-y-3">
            {data.steps.map((step) => (
              <StepCard key={step.index} step={step} />
            ))}
          </ol>
        </div>
      )}

      {data.status === "failed" && approval?.status === "approved" && hasRole("org_admin", "manager") && (
        <div className="flex items-center gap-3">
          <Button variant="secondary" disabled={retry.isPending} onClick={() => retry.mutate()}>
            {retry.isPending ? "Retrying…" : "Retry failed steps"}
          </Button>
          <span className="text-xs text-slate-500">Completed steps are not repeated.</span>
          <ErrorAlert message={errorText(retry.error)} />
        </div>
      )}
    </section>
  );
}

export function WorkflowsPage() {
  const { id } = useParams();
  const runs = useQuery({ queryKey: ["agent-runs"], queryFn: api.listRuns });

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Agent workflows</h1>
        <p className="mt-1 text-sm text-slate-500">
          Multi-step tasks with a human in the loop. Nothing is created or sent without an approval.
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <div className="space-y-6">
          <NewRun />
          <section className="rounded-xl border border-slate-200 bg-white p-2 shadow-sm">
            <h2 className="px-3 pt-2 text-sm font-semibold text-slate-700">Recent runs</h2>
            {runs.isError && <ErrorAlert message={errorText(runs.error)} />}
            {runs.data?.length === 0 && <p className="px-3 py-3 text-sm text-slate-500">No runs yet.</p>}
            <ul aria-label="Runs" className="divide-y divide-slate-100">
              {runs.data?.map((run) => (
                <li key={run.id}>
                  <Link
                    to={`/workflows/${run.id}`}
                    className={`flex items-start justify-between gap-3 rounded-md px-3 py-2.5 ${
                      run.id === id ? "bg-brand-50" : "hover:bg-slate-50"
                    }`}
                  >
                    <span className="min-w-0">
                      <span className="line-clamp-2 text-sm text-slate-800">{run.request}</span>
                      <span className="text-xs text-slate-500">
                        {run.requested_by_email} · {new Date(run.created_at).toLocaleString()}
                      </span>
                    </span>
                    <StatusChip status={run.status} />
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        </div>
        <div>{id ? <RunDetail id={id} /> : <p className="mt-10 text-center text-sm text-slate-500">Select a run, or start a new task.</p>}</div>
      </div>
    </div>
  );
}
