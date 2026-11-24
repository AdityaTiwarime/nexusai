// Shared pieces for agent runs and approvals.

import { Link } from "react-router-dom";

import type { AgentStep, ApprovalStatus, RunStatus, StepStatus } from "../api/client";
import { chunkLink } from "./rag";

export const TOOL_LABELS: Record<string, string> = {
  search_documents: "Search documents",
  create_ticket: "Create ticket",
  notify_customer: "Email customer",
};

type Tone = "ok" | "error" | "pending" | "warn" | "muted";

const TONES: Record<Tone, string> = {
  ok: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  error: "bg-rose-50 text-rose-700 ring-rose-600/20",
  pending: "bg-sky-50 text-sky-700 ring-sky-600/20",
  warn: "bg-amber-50 text-amber-800 ring-amber-600/20",
  muted: "bg-slate-100 text-slate-600 ring-slate-500/20",
};

const STATUS: Record<RunStatus | StepStatus | ApprovalStatus, { label: string; tone: Tone }> = {
  awaiting_approval: { label: "Awaiting approval", tone: "warn" },
  pending: { label: "Pending", tone: "warn" },
  executing: { label: "Executing", tone: "pending" },
  planned: { label: "Planned", tone: "muted" },
  completed: { label: "Completed", tone: "ok" },
  done: { label: "Done", tone: "ok" },
  approved: { label: "Approved", tone: "ok" },
  rejected: { label: "Rejected", tone: "muted" },
  expired: { label: "Expired", tone: "muted" },
  skipped: { label: "Skipped", tone: "muted" },
  failed: { label: "Failed", tone: "error" },
};

export function StatusChip({ status }: { status: RunStatus | StepStatus | ApprovalStatus }) {
  const { label, tone } = STATUS[status] ?? { label: status, tone: "muted" as Tone };
  return (
    <span className={`inline-flex whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONES[tone]}`}>
      {label}
    </span>
  );
}

/** Tool arguments exactly as they will run. Long text keeps its line breaks. */
const ARG_ORDER = ["query", "title", "priority", "customer_email", "description", "to", "subject", "body"];

export function ArgsTable({ args }: { args: Record<string, unknown> }) {
  // The database doesn't keep key order; show arguments in a reading order.
  const rank = (key: string) => (ARG_ORDER.includes(key) ? ARG_ORDER.indexOf(key) : ARG_ORDER.length);
  const entries = Object.entries(args).sort(([a], [b]) => rank(a) - rank(b));
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-sm">
      {entries.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="text-slate-500">{key.replace(/_/g, " ")}</dt>
          <dd className="whitespace-pre-wrap break-words text-slate-800">{String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function StepResult({ step }: { step: AgentStep }) {
  const result = step.result;
  if (!result) return null;
  if (step.tool === "search_documents") {
    const passages = (result.passages as { document_id: string; document_title: string; chunk_index: number; excerpt: string }[]) ?? [];
    if (passages.length === 0) return <p className="text-sm text-slate-500">No relevant documents found.</p>;
    return (
      <ul className="space-y-1.5">
        {passages.map((p) => (
          <li key={`${p.document_id}-${p.chunk_index}`} className="text-sm">
            <Link to={chunkLink(p.document_id, p.chunk_index)} className="font-medium text-brand-700 hover:underline">
              {p.document_title}
            </Link>
            <span className="ml-2 text-slate-600">{p.excerpt.slice(0, 160)}…</span>
          </li>
        ))}
      </ul>
    );
  }
  if (step.tool === "create_ticket") {
    return (
      <p className="text-sm text-slate-700">
        Created <span className="font-semibold">{String(result.ticket_number)}</span> ({String(result.priority)} priority)
        {result.verified === true && <span className="ml-2 text-xs text-emerald-700">✓ verified</span>}
      </p>
    );
  }
  return (
    <p className="text-sm text-slate-700">
      Recorded an email to <span className="font-medium">{String(result.to)}</span>.{" "}
      <span className="text-slate-500">{String(result.note ?? "")}</span>
      {result.verified === true && <span className="ml-2 text-xs text-emerald-700">✓ verified</span>}
    </p>
  );
}

export function StepCard({ step }: { step: AgentStep }) {
  return (
    <li className="relative rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-slate-100 text-xs font-semibold text-slate-600">
            {step.index + 1}
          </span>
          <span className="font-medium text-slate-900">{TOOL_LABELS[step.tool] ?? step.tool}</span>
          {step.mutating ? (
            <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-800">needs approval</span>
          ) : (
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-600">read-only</span>
          )}
        </div>
        <StatusChip status={step.status} />
      </div>
      <p className="mt-1 text-sm text-slate-500">{step.reason}</p>
      <div className="mt-3 rounded-md bg-slate-50 p-3">
        <ArgsTable args={step.args} />
      </div>
      {step.result && (
        <div className="mt-3">
          <StepResult step={step} />
        </div>
      )}
      {step.error && <p className="mt-2 text-sm text-rose-700">{step.error}</p>}
    </li>
  );
}

export function timeLeft(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "expired";
  const hours = Math.floor(ms / 3_600_000);
  const minutes = Math.floor((ms % 3_600_000) / 60_000);
  return hours > 0 ? `${hours} h ${minutes} min left` : `${minutes} min left`;
}
