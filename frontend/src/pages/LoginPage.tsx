import { useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Button, ErrorAlert, Field, inputClass } from "../components/ui";
import { AuthLayout } from "./AuthLayout";

export function LoginPage() {
  const { status, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (status === "authenticated") return <Navigate to={from} replace />;

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not reach the server");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout title="Sign in" subtitle="Welcome back to your organization's workspace.">
      <form onSubmit={onSubmit} className="space-y-4">
        <ErrorAlert message={error} />
        <Field label="Email">
          <input
            type="email"
            autoComplete="email"
            required
            className={inputClass}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
        <Field label="Password">
          <input
            type="password"
            autoComplete="current-password"
            required
            className={inputClass}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        <Button type="submit" disabled={submitting} className="w-full">
          {submitting ? "Signing in…" : "Sign in"}
        </Button>
      </form>
      <p className="mt-6 text-center text-sm text-slate-500">
        New to NexusAI?{" "}
        <Link to="/register" className="font-medium text-brand-600 hover:underline">
          Create an organization
        </Link>
      </p>
    </AuthLayout>
  );
}
