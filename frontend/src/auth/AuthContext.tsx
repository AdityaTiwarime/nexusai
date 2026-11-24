import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api, refreshSession, tokenStore, type Me, type RegisterInput, type Role } from "../api/client";

type Status = "loading" | "authenticated" | "anonymous";

interface AuthState {
  status: Status;
  me: Me | null;
  login: (email: string, password: string) => Promise<void>;
  register: (input: RegisterInput) => Promise<void>;
  logout: () => Promise<void>;
  hasRole: (...roles: Role[]) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<Status>("loading");
  const [me, setMe] = useState<Me | null>(null);

  const signOutLocally = useCallback(() => {
    tokenStore.set(null);
    setMe(null);
    setStatus("anonymous");
    queryClient.clear();
  }, [queryClient]);

  const loadMe = useCallback(async () => {
    const profile = await api.me();
    setMe(profile);
    setStatus("authenticated");
  }, []);

  // On page load, try to resume the session from the httpOnly refresh cookie.
  useEffect(() => {
    tokenStore.onExpired(signOutLocally);
    let cancelled = false;
    (async () => {
      const ok = await refreshSession();
      if (cancelled) return;
      if (!ok) return signOutLocally();
      try {
        await loadMe();
      } catch {
        if (!cancelled) signOutLocally();
      }
    })();
    return () => {
      cancelled = true;
      tokenStore.onExpired(null);
    };
  }, [loadMe, signOutLocally]);

  const value = useMemo<AuthState>(
    () => ({
      status,
      me,
      login: async (email, password) => {
        const tokens = await api.login(email, password);
        tokenStore.set(tokens.access_token);
        await loadMe();
      },
      register: async (input) => {
        const tokens = await api.register(input);
        tokenStore.set(tokens.access_token);
        await loadMe();
      },
      logout: async () => {
        try {
          await api.logout();
        } finally {
          signOutLocally();
        }
      },
      hasRole: (...roles) => !!me && roles.includes(me.user.role),
    }),
    [status, me, loadMe, signOutLocally],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
