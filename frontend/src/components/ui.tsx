// Small shared form primitives so every page looks and behaves the same.

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="block text-sm font-medium text-slate-700">{label}</span>
      <div className="mt-1">{children}</div>
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

export const inputClass =
  "block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20";

export function Button({
  children,
  variant = "primary",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" }) {
  const styles =
    variant === "primary"
      ? "bg-brand-600 text-white hover:bg-brand-700 disabled:bg-brand-500/60"
      : "bg-white text-slate-700 ring-1 ring-inset ring-slate-300 hover:bg-slate-50";
  return (
    <button
      className={`inline-flex items-center justify-center rounded-md px-3.5 py-2 text-sm font-medium shadow-sm transition disabled:cursor-not-allowed ${styles} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

export function ErrorAlert({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div role="alert" className="rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700 ring-1 ring-inset ring-rose-600/20">
      {message}
    </div>
  );
}

export const ROLE_LABELS: Record<string, string> = {
  super_admin: "Super admin",
  org_admin: "Org admin",
  manager: "Manager",
  employee: "Employee",
};
