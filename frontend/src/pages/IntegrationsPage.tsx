import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, ApiError, type Integration, type IntegrationKind } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { StatusBadge } from "../components/StatusBadge";
import { Button, ErrorAlert, Field, inputClass } from "../components/ui";

interface KindSpec {
  kind: IntegrationKind;
  label: string;
  description: string;
  usedBy: string;
  secrets: { key: string; label: string; type?: string }[];
  config: { key: string; label: string }[];
  // Values for the bundled mock services (docker compose), to try things out quickly.
  demo: { name: string; base_url: string; config: Record<string, string>; secrets: Record<string, string> };
}

const KINDS: KindSpec[] = [
  {
    kind: "servicenow",
    label: "ServiceNow",
    description: "Tickets created by agents become ServiceNow incidents; resolutions come back via a signed webhook.",
    usedBy: "create_ticket",
    secrets: [
      { key: "username", label: "Username" },
      { key: "password", label: "Password", type: "password" },
    ],
    config: [],
    demo: {
      name: "ServiceNow (demo)",
      base_url: "http://mock-services:9000",
      config: {},
      secrets: { username: "nexus", password: "servicenow-demo-password" },
    },
  },
  {
    kind: "salesforce",
    label: "Salesforce",
    description: "Agents look up the customer (account, tier, owner) before acting. OAuth 2.0 client credentials.",
    usedBy: "lookup_customer",
    secrets: [
      { key: "client_id", label: "Client ID" },
      { key: "client_secret", label: "Client secret", type: "password" },
    ],
    config: [],
    demo: {
      name: "Salesforce (demo)",
      base_url: "http://mock-services:9000",
      config: {},
      secrets: { client_id: "nexus-client", client_secret: "salesforce-demo-secret" },
    },
  },
  {
    kind: "email",
    label: "Email",
    description: "Customer emails approved in workflows are actually sent instead of only recorded in the outbox.",
    usedBy: "notify_customer",
    secrets: [{ key: "api_key", label: "API key", type: "password" }],
    config: [{ key: "sender", label: "Sender address" }],
    demo: {
      name: "Support mailbox (demo)",
      base_url: "http://mock-services:9000",
      config: { sender: "support@nexusai-demo.com" },
      secrets: { api_key: "email-demo-key" },
    },
  },
];

function errorText(err: unknown): string | null {
  if (!err) return null;
  return err instanceof ApiError ? err.detail : "Something went wrong";
}

function Health({ integration }: { integration: Integration }) {
  if (integration.status === "disabled") return <StatusBadge tone="pending" label="Disabled" />;
  if (integration.health_status === "ok") return <StatusBadge tone="ok" label="Connected" />;
  if (integration.health_status === "error") return <StatusBadge tone="error" label="Failing" />;
  return <StatusBadge tone="pending" label="Not checked" />;
}

function ConnectForm({ spec, onCreated }: { spec: KindSpec; onCreated: (i: Integration) => void }) {
  const queryClient = useQueryClient();
  const empty = { name: spec.label, base_url: "", config: {} as Record<string, string>, secrets: {} as Record<string, string> };
  const [form, setForm] = useState(empty);
  const create = useMutation({
    mutationFn: () => api.createIntegration({ kind: spec.kind, ...form }),
    onSuccess: (created) => {
      setForm(empty);
      onCreated(created);
      queryClient.invalidateQueries({ queryKey: ["integrations"] });
    },
  });

  return (
    <form
      className="mt-4 space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate();
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={`${spec.label} name`}>
          <input required className={inputClass} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </Field>
        <Field label={`${spec.label} base URL`} hint="Only hosts allowed by the administrator can be used.">
          <input
            required
            className={inputClass}
            placeholder="https://your-instance.example.com"
            value={form.base_url}
            onChange={(e) => setForm({ ...form, base_url: e.target.value })}
          />
        </Field>
        {spec.config.map((c) => (
          <Field key={c.key} label={c.label}>
            <input
              required
              className={inputClass}
              value={form.config[c.key] ?? ""}
              onChange={(e) => setForm({ ...form, config: { ...form.config, [c.key]: e.target.value } })}
            />
          </Field>
        ))}
        {spec.secrets.map((s) => (
          <Field key={s.key} label={s.label} hint="Stored encrypted. Never shown again.">
            <input
              required
              type={s.type ?? "text"}
              autoComplete="off"
              className={inputClass}
              value={form.secrets[s.key] ?? ""}
              onChange={(e) => setForm({ ...form, secrets: { ...form.secrets, [s.key]: e.target.value } })}
            />
          </Field>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={create.isPending}>
          {create.isPending ? "Connecting…" : `Connect ${spec.label}`}
        </Button>
        <button
          type="button"
          className="text-sm text-brand-700 hover:underline"
          onClick={() => setForm({ ...spec.demo, config: { ...spec.demo.config }, secrets: { ...spec.demo.secrets } })}
        >
          Fill in the bundled mock service
        </button>
      </div>
      <ErrorAlert message={errorText(create.error)} />
    </form>
  );
}

function CallLog({ integration }: { integration: Integration }) {
  const calls = useQuery({
    queryKey: ["integration-calls", integration.id],
    queryFn: () => api.integrationCalls(integration.id),
  });
  if (!calls.data || calls.data.length === 0) return <p className="text-sm text-slate-500">No calls yet.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] text-left text-xs">
        <thead className="text-slate-500">
          <tr>
            <th className="pb-1 pr-3 font-medium">Operation</th>
            <th className="pb-1 pr-3 font-medium">Result</th>
            <th className="pb-1 pr-3 font-medium">HTTP</th>
            <th className="pb-1 pr-3 text-right font-medium">Attempts</th>
            <th className="pb-1 pr-3 text-right font-medium">Latency</th>
            <th className="pb-1 font-medium">When</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {calls.data.slice(0, 8).map((call) => (
            <tr key={call.id}>
              <td className="py-1.5 pr-3 font-mono">{call.operation}</td>
              <td className={`py-1.5 pr-3 ${call.outcome === "success" ? "text-emerald-700" : "text-rose-700"}`}>
                {call.outcome}
                {call.error && <span className="block text-slate-500">{call.error}</span>}
              </td>
              <td className="py-1.5 pr-3 tabular-nums">{call.http_status ?? "—"}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{call.attempts}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{call.latency_ms} ms</td>
              <td className="py-1.5 whitespace-nowrap text-slate-500">{new Date(call.created_at).toLocaleTimeString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ConnectedCard({ integration, spec }: { integration: Integration; spec: KindSpec }) {
  const { hasRole } = useAuth();
  const isAdmin = hasRole("org_admin");
  const queryClient = useQueryClient();
  const [secret, setSecret] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["integrations"] });
    queryClient.invalidateQueries({ queryKey: ["integration-calls", integration.id] });
  };
  const test = useMutation({ mutationFn: () => api.testIntegration(integration.id), onSuccess: refresh });
  const toggle = useMutation({
    mutationFn: () =>
      api.updateIntegration(integration.id, { status: integration.status === "active" ? "disabled" : "active" }),
    onSuccess: refresh,
  });
  const rotate = useMutation({
    mutationFn: () => api.rotateWebhookSecret(integration.id),
    onSuccess: (updated) => {
      setSecret(updated.webhook_secret ?? null);
      refresh();
    },
  });
  const remove = useMutation({ mutationFn: () => api.deleteIntegration(integration.id), onSuccess: refresh });
  const error = errorText(test.error) ?? errorText(toggle.error) ?? errorText(rotate.error) ?? errorText(remove.error);

  return (
    <div className="mt-4 space-y-4">
      <dl className="grid gap-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-xs uppercase tracking-wide text-slate-500">Name</dt>
          <dd className="text-slate-800">{integration.name}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-slate-500">Base URL</dt>
          <dd className="break-all font-mono text-xs text-slate-800">{integration.base_url}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-slate-500">Credentials</dt>
          <dd className="text-slate-800">
            {integration.secret_fields.map((f) => (
              <span key={f} className="mr-2">
                {f.replace(/_/g, " ")}: ••••••
              </span>
            ))}
          </dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-slate-500">Last checked</dt>
          <dd className="text-slate-800">
            {integration.health_checked_at ? new Date(integration.health_checked_at).toLocaleString() : "Never"}
          </dd>
        </div>
        {typeof integration.config.sender === "string" && (
          <div>
            <dt className="text-xs uppercase tracking-wide text-slate-500">Sender</dt>
            <dd className="text-slate-800">{integration.config.sender}</dd>
          </div>
        )}
        {integration.webhook_url && (
          <div className="sm:col-span-2">
            <dt className="text-xs uppercase tracking-wide text-slate-500">Webhook URL (configure in {spec.label})</dt>
            <dd className="break-all font-mono text-xs text-slate-800">{integration.webhook_url}</dd>
          </div>
        )}
      </dl>
      {integration.last_error && <ErrorAlert message={integration.last_error} />}
      {secret && (
        <div className="rounded-md bg-amber-50 p-3 text-sm text-amber-900 ring-1 ring-inset ring-amber-600/20">
          <p className="font-medium">Webhook signing secret (shown once, copy it now):</p>
          <code className="mt-1 block break-all font-mono text-xs">{secret}</code>
        </div>
      )}
      {isAdmin && (
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" disabled={test.isPending} onClick={() => test.mutate()}>
            {test.isPending ? "Testing…" : "Test connection"}
          </Button>
          <Button variant="secondary" disabled={toggle.isPending} onClick={() => toggle.mutate()}>
            {integration.status === "active" ? "Disable" : "Enable"}
          </Button>
          {integration.kind === "servicenow" && (
            <Button variant="secondary" disabled={rotate.isPending} onClick={() => rotate.mutate()}>
              Rotate webhook secret
            </Button>
          )}
          {confirmDelete ? (
            <>
              <Button className="bg-rose-600 hover:bg-rose-700" onClick={() => remove.mutate()}>
                Yes, disconnect
              </Button>
              <Button variant="secondary" onClick={() => setConfirmDelete(false)}>
                Cancel
              </Button>
            </>
          ) : (
            <Button variant="secondary" className="text-rose-700" onClick={() => setConfirmDelete(true)}>
              Disconnect
            </Button>
          )}
        </div>
      )}
      <ErrorAlert message={error} />
      <div>
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">Recent calls</p>
        <CallLog integration={integration} />
      </div>
    </div>
  );
}

export function IntegrationsPage() {
  const { hasRole } = useAuth();
  const integrations = useQuery({ queryKey: ["integrations"], queryFn: api.listIntegrations });
  const [newSecret, setNewSecret] = useState<{ kind: string; secret: string } | null>(null);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Integrations</h1>
        <p className="mt-1 text-sm text-slate-500">
          Connect the systems agents act on. Credentials are encrypted at rest, calls are retried safely and logged,
          and webhooks are verified with a signature.
        </p>
      </header>
      {integrations.isError && <ErrorAlert message={errorText(integrations.error)} />}
      {newSecret && (
        <div className="rounded-md bg-amber-50 p-4 text-sm text-amber-900 ring-1 ring-inset ring-amber-600/20">
          <p className="font-medium">Connected. Webhook signing secret (shown once, copy it now):</p>
          <code className="mt-1 block break-all font-mono text-xs">{newSecret.secret}</code>
        </div>
      )}
      {KINDS.map((spec) => {
        const integration = integrations.data?.find((i) => i.kind === spec.kind);
        return (
          <section key={spec.kind} aria-label={spec.label} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold">{spec.label}</h2>
                <p className="mt-1 text-sm text-slate-500">{spec.description}</p>
                <p className="mt-1 text-xs text-slate-400">
                  Used by the <code>{spec.usedBy}</code> agent tool.
                </p>
              </div>
              {integration ? <Health integration={integration} /> : <StatusBadge tone="pending" label="Not connected" />}
            </div>
            {integration ? (
              <ConnectedCard integration={integration} spec={spec} />
            ) : hasRole("org_admin") ? (
              <ConnectForm
                spec={spec}
                onCreated={(created) =>
                  created.webhook_secret && setNewSecret({ kind: created.kind, secret: created.webhook_secret })
                }
              />
            ) : (
              <p className="mt-3 text-sm text-slate-500">An org admin can connect {spec.label}.</p>
            )}
          </section>
        );
      })}
    </div>
  );
}
