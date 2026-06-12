import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, getToken, onUnauthorized, setToken } from "@/lib/apiClient";

export type Role = "admin" | "driver" | "parent";

export interface AuthUser {
  id: string;
  email: string;
  fullName: string | null;
  role: Role | null;
}

interface AuthContextValue {
  user: AuthUser | null;
  role: Role | null;
  loading: boolean;
  signIn: (token: string, user: AuthUser) => void;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const me = await api.get("/api/auth/me");
      setUser({ id: me.id, email: me.email, fullName: me.fullName ?? null, role: me.role ?? null });
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const signIn = useCallback((token: string, nextUser: AuthUser) => {
    setToken(token);
    setUser(nextUser);
    setLoading(false);
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.post("/api/auth/logout");
    } catch {
      // ignore network errors on logout
    }
    setToken(null);
    setUser(null);
    // Drop all cached queries so the next user can't see the previous one's data.
    queryClient.clear();
  }, [queryClient]);

  useEffect(() => {
    onUnauthorized(() => {
      setUser(null);
      queryClient.clear();
    });
    void refresh();
  }, [refresh, queryClient]);

  const value = useMemo<AuthContextValue>(
    () => ({ user, role: user?.role ?? null, loading, signIn, signOut, refresh }),
    [user, loading, signIn, signOut, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
