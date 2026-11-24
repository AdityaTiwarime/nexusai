import { Navigate, Outlet, useLocation } from "react-router-dom";

import type { Role } from "../api/client";
import { useAuth } from "./AuthContext";

/** Route guard. UX only: the API enforces the same rules on every request. */
export function RequireAuth({ roles }: { roles?: Role[] }) {
  const { status, hasRole } = useAuth();
  const location = useLocation();

  if (status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-slate-500">
        Loading…
      </div>
    );
  }
  if (status === "anonymous") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (roles && !hasRole(...roles)) {
    return <Navigate to="/" replace />;
  }
  return <Outlet />;
}
