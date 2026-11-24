import { Route, Routes } from "react-router-dom";

import { AuthProvider } from "./auth/AuthContext";
import { RequireAuth } from "./auth/RequireAuth";
import { Layout } from "./components/Layout";
import { AuditLogsPage } from "./pages/AuditLogsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { RegisterPage } from "./pages/RegisterPage";
import { UsersPage } from "./pages/UsersPage";

export function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route element={<RequireAuth />}>
          <Route element={<Layout />}>
            <Route index element={<DashboardPage />} />
            <Route element={<RequireAuth roles={["org_admin", "manager"]} />}>
              <Route path="users" element={<UsersPage />} />
            </Route>
            <Route element={<RequireAuth roles={["org_admin"]} />}>
              <Route path="audit-logs" element={<AuditLogsPage />} />
            </Route>
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Route>
      </Routes>
    </AuthProvider>
  );
}
