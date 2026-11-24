import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, ApiError, type NewUserInput, type User } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { StatusBadge } from "../components/StatusBadge";
import { Button, ErrorAlert, Field, inputClass, ROLE_LABELS } from "../components/ui";

const ASSIGNABLE_ROLES = ["employee", "manager", "org_admin"] as const;

function errorText(err: unknown): string | null {
  if (!err) return null;
  return err instanceof ApiError ? err.detail : "Something went wrong";
}

function NewUserForm() {
  const queryClient = useQueryClient();
  const empty: NewUserInput = { email: "", full_name: "", password: "", role: "employee" };
  const [form, setForm] = useState<NewUserInput>(empty);
  const create = useMutation({
    mutationFn: api.createUser,
    onSuccess: () => {
      setForm(empty);
      queryClient.invalidateQueries({ queryKey: ["users"] });
    },
  });

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate(form);
      }}
      className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
    >
      <h2 className="text-base font-semibold">Add a team member</h2>
      <p className="mt-1 text-sm text-slate-500">
        Share the temporary password with them securely. They'll sign in with their email.
      </p>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <Field label="Full name">
          <input required className={inputClass} value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
        </Field>
        <Field label="Email">
          <input type="email" required className={inputClass} value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        </Field>
        <Field label="Temporary password" hint="At least 12 characters.">
          <input type="password" required minLength={12} autoComplete="new-password" className={inputClass} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        </Field>
        <Field label="Role">
          <select className={inputClass} value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as NewUserInput["role"] })}>
            {ASSIGNABLE_ROLES.map((r) => (
              <option key={r} value={r}>{ROLE_LABELS[r]}</option>
            ))}
          </select>
        </Field>
      </div>
      <div className="mt-4 flex items-center gap-3">
        <Button type="submit" disabled={create.isPending}>
          {create.isPending ? "Adding…" : "Add user"}
        </Button>
        {create.isSuccess && <span className="text-sm text-emerald-700">User added.</span>}
      </div>
      <div className="mt-3">
        <ErrorAlert message={errorText(create.error)} />
      </div>
    </form>
  );
}

function UserRow({ user, canManage, isSelf }: { user: User; canManage: boolean; isSelf: boolean }) {
  const queryClient = useQueryClient();
  const update = useMutation({
    mutationFn: (input: Parameters<typeof api.updateUser>[1]) => api.updateUser(user.id, input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["users"] }),
  });
  const editable = canManage && !isSelf;

  return (
    <tr className="align-middle">
      <td className="py-3 pr-4">
        <div className="font-medium text-slate-900">
          {user.full_name} {isSelf && <span className="text-xs text-slate-400">(you)</span>}
        </div>
        <div className="text-xs text-slate-500">{user.email}</div>
      </td>
      <td className="py-3 pr-4">
        {editable ? (
          <select
            aria-label={`Role for ${user.email}`}
            className={`${inputClass} w-36 py-1.5`}
            value={user.role}
            disabled={update.isPending}
            onChange={(e) => update.mutate({ role: e.target.value as NewUserInput["role"] })}
          >
            {ASSIGNABLE_ROLES.map((r) => (
              <option key={r} value={r}>{ROLE_LABELS[r]}</option>
            ))}
          </select>
        ) : (
          <span className="text-sm text-slate-700">{ROLE_LABELS[user.role]}</span>
        )}
      </td>
      <td className="py-3 pr-4">
        <StatusBadge tone={user.is_active ? "ok" : "pending"} label={user.is_active ? "Active" : "Deactivated"} />
      </td>
      <td className="py-3 pr-4 text-xs text-slate-500">
        {user.last_login_at ? new Date(user.last_login_at).toLocaleString() : "Never"}
      </td>
      <td className="py-3 text-right">
        {editable && (
          <Button variant="secondary" disabled={update.isPending} onClick={() => update.mutate({ is_active: !user.is_active })}>
            {user.is_active ? "Deactivate" : "Reactivate"}
          </Button>
        )}
        {update.error && <div className="mt-1 text-xs text-rose-600">{errorText(update.error)}</div>}
      </td>
    </tr>
  );
}

export function UsersPage() {
  const { me, hasRole } = useAuth();
  const canManage = hasRole("org_admin");
  const users = useQuery({ queryKey: ["users"], queryFn: api.listUsers });

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Users</h1>
        <p className="mt-1 text-sm text-slate-500">
          People in {me?.organization?.name ?? "your organization"}.
          {!canManage && " Only org admins can add or change users."}
        </p>
      </header>

      {canManage && <NewUserForm />}

      <section className="overflow-hidden rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        {users.isPending ? (
          <p className="text-sm text-slate-500">Loading users…</p>
        ) : users.isError ? (
          <ErrorAlert message={errorText(users.error)} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left">
              <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="pb-2 pr-4 font-medium">User</th>
                  <th className="pb-2 pr-4 font-medium">Role</th>
                  <th className="pb-2 pr-4 font-medium">Status</th>
                  <th className="pb-2 pr-4 font-medium">Last sign-in</th>
                  <th className="pb-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {users.data.map((u) => (
                  <UserRow key={u.id} user={u} canManage={canManage} isSelf={u.id === me?.user.id} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
