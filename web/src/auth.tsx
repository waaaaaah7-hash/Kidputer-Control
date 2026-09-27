import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api, ApiError, UNAUTHORIZED_EVENT, type Admin } from "./api";
import { Spinner } from "./ui";

interface AuthState {
  admin: Admin | null;
  loading: boolean;
  refresh: () => Promise<Admin | null>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [admin, setAdmin] = useState<Admin | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const me = await api.me();
      setAdmin(me);
      return me;
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 401) console.error(e);
      setAdmin(null);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setAdmin(null);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const onUnauthorized = () => setAdmin(null);
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [refresh]);

  return <AuthContext.Provider value={{ admin, loading, refresh, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}

/** Routes behind sign-in. Two-factor authentication must be set up before anything else. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { admin, loading } = useAuth();
  const location = useLocation();
  if (loading) return <Spinner />;
  if (!admin) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (!admin.totp_enabled) return <Navigate to="/setup-2fa" replace />;
  return <>{children}</>;
}
