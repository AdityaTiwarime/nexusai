import { NavLink, Outlet } from "react-router-dom";

import type { Role } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { ROLE_LABELS } from "./ui";

interface NavItem {
  label: string;
  to?: string;
  phase?: number;
  roles?: Role[];
}

// Pages arrive phase by phase; upcoming ones are shown but disabled so the
// product's shape is visible from day one. Role-gated items are hidden for
// users who can't use them (the API enforces the same rules).
const NAV: NavItem[] = [
  { label: "Dashboard", to: "/" },
  { label: "AI Chat", phase: 4 },
  { label: "Documents", to: "/documents", roles: ["org_admin", "manager", "employee"] },
  { label: "Search", phase: 4 },
  { label: "Agent Workflows", phase: 5 },
  { label: "Approvals", phase: 5 },
  { label: "Integrations", phase: 6 },
  { label: "Users", to: "/users", roles: ["org_admin", "manager"] },
  { label: "Audit Logs", to: "/audit-logs", roles: ["org_admin"] },
  { label: "Observability", phase: 8 },
  { label: "Incidents", phase: 9 },
];

export function Layout() {
  const { me, hasRole, logout } = useAuth();
  const items = NAV.filter((item) => !item.roles || hasRole(...item.roles));

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-64 shrink-0 flex-col border-r border-slate-200 bg-white md:flex">
        <div className="flex h-16 items-center gap-2 border-b border-slate-200 px-5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-sm font-bold text-white">
            N
          </div>
          <span className="text-lg font-semibold tracking-tight">NexusAI</span>
        </div>
        <nav aria-label="Main" className="flex-1 space-y-0.5 p-3">
          {items.map((item) =>
            item.to ? (
              <NavLink
                key={item.label}
                to={item.to}
                end={item.to === "/"}
                className={({ isActive }) =>
                  `block rounded-md px-3 py-2 text-sm font-medium ${
                    isActive ? "bg-brand-50 text-brand-700" : "text-slate-700 hover:bg-slate-100"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ) : (
              <span
                key={item.label}
                aria-disabled="true"
                className="flex cursor-not-allowed items-center justify-between rounded-md px-3 py-2 text-sm text-slate-400"
              >
                {item.label}
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500">
                  Phase {item.phase}
                </span>
              </span>
            ),
          )}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 items-center justify-between gap-4 border-b border-slate-200 bg-white px-6">
          <span className="text-sm font-medium text-slate-700">
            <span className="md:hidden">NexusAI · </span>
            {me?.organization?.name ?? "Platform administration"}
          </span>
          <div className="flex items-center gap-4">
            <div className="text-right leading-tight">
              <div className="text-sm font-medium text-slate-900">{me?.user.full_name}</div>
              <div className="text-xs text-slate-500">{me ? ROLE_LABELS[me.user.role] : ""}</div>
            </div>
            <button
              onClick={() => void logout()}
              className="rounded-md px-3 py-1.5 text-sm text-slate-600 ring-1 ring-inset ring-slate-300 hover:bg-slate-50"
            >
              Sign out
            </button>
          </div>
        </header>
        <main className="flex-1 p-6 lg:p-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
