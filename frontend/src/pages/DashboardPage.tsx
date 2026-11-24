import { useQuery } from "@tanstack/react-query";

import { api, type CheckResult } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";

const REFRESH_MS = 15_000;

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-sm font-medium text-slate-500">{title}</h2>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function DependencyRow({ name, check }: { name: string; check: CheckResult }) {
  return (
    <li className="flex items-center justify-between py-2">
      <span className="text-sm font-medium capitalize text-slate-800">{name}</span>
      <span className="flex items-center gap-3">
        {check.latency_ms !== null && (
          <span className="text-xs tabular-nums text-slate-500">{check.latency_ms} ms</span>
        )}
        {check.error && <span className="text-xs text-rose-600">{check.error}</span>}
        <StatusBadge
          tone={check.status === "ok" ? "ok" : "error"}
          label={check.status === "ok" ? "Healthy" : "Down"}
        />
      </span>
    </li>
  );
}

export function DashboardPage() {
  const live = useQuery({ queryKey: ["health", "live"], queryFn: api.live, refetchInterval: REFRESH_MS });
  const ready = useQuery({ queryKey: ["health", "ready"], queryFn: api.ready, refetchInterval: REFRESH_MS });
  const info = useQuery({ queryKey: ["system", "info"], queryFn: api.systemInfo });

  return (
    <div className="mx-auto max-w-5xl">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">System overview</h1>
        <p className="mt-1 text-sm text-slate-500">
          Live status of the NexusAI platform. Refreshes every {REFRESH_MS / 1000} seconds.
        </p>
      </header>

      <div className="grid gap-4 md:grid-cols-3">
        <Card title="API">
          {live.isPending ? (
            <StatusBadge tone="pending" label="Checking…" />
          ) : live.isError ? (
            <StatusBadge tone="error" label="Unreachable" />
          ) : (
            <StatusBadge tone="ok" label="Online" />
          )}
        </Card>

        <Card title="Readiness">
          {ready.isPending ? (
            <StatusBadge tone="pending" label="Checking…" />
          ) : ready.isError ? (
            <StatusBadge tone="error" label="Unknown" />
          ) : (
            <StatusBadge
              tone={ready.data.status === "ok" ? "ok" : "error"}
              label={ready.data.status === "ok" ? "Ready" : "Degraded"}
            />
          )}
        </Card>

        <Card title="Build">
          {info.data ? (
            <p className="text-sm text-slate-700">
              v{info.data.version} · <span className="font-medium">{info.data.environment}</span>
            </p>
          ) : (
            <p className="text-sm text-slate-400">{info.isError ? "Unavailable" : "Loading…"}</p>
          )}
        </Card>
      </div>

      <section className="mt-6 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-base font-semibold">Dependencies</h2>
        {ready.data ? (
          <ul className="mt-2 divide-y divide-slate-100">
            {Object.entries(ready.data.checks).map(([name, check]) => (
              <DependencyRow key={name} name={name} check={check} />
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-sm text-slate-500">
            {ready.isError
              ? "Could not reach the API. Is the backend running?"
              : "Loading dependency checks…"}
          </p>
        )}
      </section>
    </div>
  );
}
