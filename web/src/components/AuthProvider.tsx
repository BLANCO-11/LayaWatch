/* Auth guard: redirects to /login on 401, returns to the original path
 * after sign-in. Contract: plan task 6, api-reference section 10.
 * Static-export safe: the guard is client-side (R-05); the server 401s
 * every API call regardless of route.
 * Phase 6: one fetch per navigation shared through context, so views and the
 * sidebar gate from a single /auth/me payload (useAuth reads it). */
"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, ApiError, type MeResponse } from "@/lib/api";

export interface AuthState {
  me: MeResponse | null;
  loading: boolean;
}

const AuthContext = createContext<AuthState>({ me: null, loading: true });

export function useAuth(): AuthState {
  return useContext(AuthContext);
}

export default function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>({ me: null, loading: true });
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    let cancelled = false;
    if (pathname === "/login" || pathname === "/setup") {
      setState({ me: null, loading: false });
      return;
    }
    api
      .get<MeResponse>("/api/v1/auth/me")
      .then((mine) => {
        if (!cancelled) setState({ me: mine, loading: false });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setState({ me: null, loading: false });
        if (err instanceof ApiError && err.status === 401) {
          router.replace(`/login?next=${encodeURIComponent(pathname)}`);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [pathname, router]);

  if (state.loading) {
    return (
      <div className="lw-app">
        <main className="lw-content" aria-busy="true" aria-label="Checking session">
          <div className="lw-skeleton" style={{ height: 20, width: 220 }} />
          <div className="lw-skeleton" style={{ height: 12, width: "60%", marginTop: 12 }} />
        </main>
      </div>
    );
  }
  return <AuthContext.Provider value={state}>{children}</AuthContext.Provider>;
}
