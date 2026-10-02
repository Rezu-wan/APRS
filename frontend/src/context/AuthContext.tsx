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
import { useNavigate } from "react-router-dom";
import { getMe, getMeWithKey } from "../api/auth";
import { clearSession, getSession, setSession } from "../lib/session";
import type { MeResponse, Role } from "../types/api";

interface AuthUser {
  role: Role;
  keyName: string;
}

interface AuthContextValue {
  user: AuthUser | null;
  isLoading: boolean;
  login: (apiKey: string) => Promise<MeResponse>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);
// Exported for test utilities that stub the context value directly.
export { AuthContext };

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  // On mount: validate any stored session against the backend.
  useEffect(() => {
    let cancelled = false;
    const session = getSession();
    if (!session) {
      setIsLoading(false);
      return;
    }
    getMe()
      .then((me) => {
        if (!cancelled) setUser({ role: me.role, keyName: me.key_name });
      })
      .catch(() => {
        // 401 already cleared the session in the client; clear defensively here
        // for network errors and any other failure mode.
        clearSession();
        if (!cancelled) setUser(null);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(
    async (apiKey: string): Promise<MeResponse> => {
      // Drop any stale session first so a failed attempt never triggers a
      // 401 redirect/reload in the API client.
      clearSession();
      const me = await getMeWithKey(apiKey);
      setSession({ apiKey, role: me.role, keyName: me.key_name });
      setUser({ role: me.role, keyName: me.key_name });
      return me;
    },
    []
  );

  const logout = useCallback(() => {
    clearSession();
    setUser(null);
    queryClient.clear();
    navigate("/login");
  }, [queryClient, navigate]);

  const value = useMemo(
    () => ({ user, isLoading, login, logout }),
    [user, isLoading, login, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
