"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { useT } from "./i18n";
import { api, setAccessToken, tryRefresh } from "./api";
import type { ProfilePrivate, UserOut } from "./types";

interface AuthState {
  user: UserOut | null;
  profile: ProfilePrivate | null;
  loading: boolean;
  login: (phoneNumber: string, password: string) => Promise<void>;
  register: (input: {
    phone_number: string;
    password: string;
    nickname: string;
  }) => Promise<void>;
  logout: () => Promise<void>;
  refreshProfile: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserOut | null>(null);
  const [profile, setProfile] = useState<ProfilePrivate | null>(null);
  const [loading, setLoading] = useState(true);

  const loadIdentity = useCallback(async () => {
    const [u, p] = await Promise.all([api.me(), api.myProfile()]);
    setUser(u);
    setProfile(p);
  }, []);

  // Silent session restore from the HttpOnly refresh cookie.
  useEffect(() => {
    (async () => {
      try {
        if (await tryRefresh()) await loadIdentity();
      } catch {
        /* not signed in */
      } finally {
        setLoading(false);
      }
    })();
  }, [loadIdentity]);

  const login = useCallback(
    async (phoneNumber: string, password: string) => {
      const res = await api.login({ phone_number: phoneNumber, password });
      setAccessToken(res.access_token);
      await loadIdentity();
    },
    [loadIdentity],
  );

  const register = useCallback(
    async (input: { phone_number: string; password: string; nickname: string }) => {
      const res = await api.register({
        ...input,
        consent_privacy: true,
        consent_terms: true,
      });
      setAccessToken(res.access_token);
      await loadIdentity();
    },
    [loadIdentity],
  );

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setAccessToken(null);
      setUser(null);
      setProfile(null);
    }
  }, []);

  const value = useMemo<AuthState>(
    () => ({ user, profile, loading, login, register, logout, refreshProfile: loadIdentity }),
    [user, profile, loading, login, register, logout, loadIdentity],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

/** Gate a page behind authentication, showing a friendly prompt otherwise. */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const t = useT();

  if (loading) {
    return (
      <div className="py-24 text-center text-sm text-muted-foreground">
        {t("common.loading")}
      </div>
    );
  }

  if (!user) {
    return (
      <div className="mx-auto max-w-md py-24 text-center">
        <h2 className="text-xl font-semibold">{t("authGate.title")}</h2>
        <p className="mt-2 text-sm text-muted-foreground">{t("authGate.body")}</p>
        <Button asChild className="mt-6">
          <Link href="/login">{t("authGate.action")}</Link>
        </Button>
      </div>
    );
  }

  return <>{children}</>;
}
