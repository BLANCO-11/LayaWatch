/* Auth guard: redirects to /login on 401, returns to the original path
 * after sign-in. Contract: plan task 6, api-reference section 10.
 * Static-export safe: the guard is client-side (R-05); the server 401s
 * every API call regardless of route. */
"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, ApiError, type MeResponse } from "@/lib/api";

export interface AuthState {
  me: MeResponse | null;
  loading: boolean;
}

export function useAuth(): AuthState {
  const [me, setMe] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    let cancelled = false;
    if (pathname === "/login" || pathname === "/setup") {
      setLoading(false);
      return;
    }
    api
      .get<MeResponse>("/api/v1/auth/me")
      .then((mine) => {
        if (!cancelled) {
          setMe(mine);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoading(false);
        if (err instanceof ApiError && err.status === 401) {
          router.replace(`/login?next=${encodeURIComponent(pathname)}`);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [pathname, router]);

  return { me, loading };
}

export default function AuthProvider({ children }: { children: React.ReactNode }) {
  const { loading } = useAuth();
  if (loading) {
    return (
      <div className="lw-app">
        <main className="lw-content" aria-busy="true" aria-label="Checking session">
          <div className="lw-skeleton" style={{ height: 20, width: 220 }} />
          <div className="lw-skeleton" style={{ height: 12, width: "60%", marginTop: 12 }} />
        </main>
      </div>
    );
  }
  return <>{children}</>;
}
