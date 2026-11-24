import { useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Button, ErrorAlert, Field, inputClass } from "../components/ui";
import { AuthLayout } from "./AuthLayout";

export function RegisterPage() {
  const { status, register } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({
    organization_name: "",
    full_name: "",
    email: "",
    password: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (status === "authenticated") return <Navigate to="/" replace />;

  const update = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await register(form);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not reach the server");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout
      title="Create your organization"
      subtitle="You'll be its first admin and can invite your team afterwards."
    >
      <form onSubmit={onSubmit} className="space-y-4">
        <ErrorAlert message={error} />
        <Field label="Organization name">
          <input required className={inputClass} value={form.organization_name} onChange={update("organization_name")} />
        </Field>
        <Field label="Your name">
          <input required autoComplete="name" className={inputClass} value={form.full_name} onChange={update("full_name")} />
        </Field>
        <Field label="Work email">
          <input type="email" required autoComplete="email" className={inputClass} value={form.email} onChange={update("email")} />
        </Field>
        <Field label="Password" hint="At least 12 characters, and not containing your email.">
          <input
            type="password"
            required
            minLength={12}
            autoComplete="new-password"
            className={inputClass}
            value={form.password}
            onChange={update("password")}
          />
        </Field>
        <Button type="submit" disabled={submitting} className="w-full">
          {submitting ? "Creating…" : "Create organization"}
        </Button>
      </form>
      <p className="mt-6 text-center text-sm text-slate-500">
        Already have an account?{" "}
        <Link to="/login" className="font-medium text-brand-600 hover:underline">
          Sign in
        </Link>
      </p>
    </AuthLayout>
  );
}
