import { NavLink, Outlet } from "react-router-dom";

interface NavItem {
  label: string;
  to?: string;
  phase?: number;
}

// Pages arrive phase by phase; upcoming ones are shown but disabled so the
// product's shape is visible from day one.
const NAV: NavItem[] = [
  { label: "Dashboard", to: "/" },
  { label: "AI Chat", phase: 4 },
  { label: "Documents", phase: 3 },
  { label: "Search", phase: 4 },
  { label: "Agent Workflows", phase: 5 },
  { label: "Approvals", phase: 5 },
  { label: "Integrations", phase: 6 },
  { label: "Audit Logs", phase: 2 },
  { label: "Observability", phase: 8 },
  { label: "Incidents", phase: 9 },
  { label: "Settings", phase: 2 },
];

export function Layout() {
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
          {NAV.map((item) =>
            item.to ? (
              <NavLink
                key={item.label}
                to={item.to}
                end
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
      <div className="flex flex-1 flex-col">
        <header className="flex h-16 items-center border-b border-slate-200 bg-white px-6 md:hidden">
          <span className="text-lg font-semibold">NexusAI</span>
        </header>
        <main className="flex-1 p-6 lg:p-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
