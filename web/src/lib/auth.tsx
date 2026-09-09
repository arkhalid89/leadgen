"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "./api";
import type { User } from "./types";

interface AuthState {
  user: User | null;
  loading: boolean;
  refresh: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName: string) => Promise<void>;
  logout: () => Promise<void>;
  activate: (key: string) => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  const refresh = useCallback(async () => {
    try {
      setUser(await api.get<User>("/api/auth/me"));
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) setUser(null);
      else throw error;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(
    async (email: string, password: string) => {
      setUser(await api.post<User>("/api/auth/login", { email, password }));
      router.push("/");
    },
    [router],
  );

  const register = useCallback(
    async (email: string, password: string, fullName: string) => {
      setUser(await api.post<User>("/api/auth/register", { email, password, full_name: fullName }));
      router.push("/activate");
    },
    [router],
  );

  const logout = useCallback(async () => {
    await api.post("/api/auth/logout");
    setUser(null);
    router.push("/login");
  }, [router]);

  const activate = useCallback(async (key: string) => {
    setUser(await api.post<User>("/api/auth/activate", { license_key: key }));
  }, []);

  const value = useMemo(
    () => ({ user, loading, refresh, login, register, logout, activate }),
    [user, loading, refresh, login, register, logout, activate],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
