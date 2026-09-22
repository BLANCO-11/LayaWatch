/* Login screen. Posts POST /api/v1/auth/login (api-reference section 10),
 * renders the 429 Retry-After message, then returns to the ?next= path. */
"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { Input } from "@/components/ui/Input";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import "../auth.css";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next") || "/";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | undefined>(undefined);
  const [working, setWorking] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setWorking(true);
    setError(undefined);
    try {
      await api.post("/api/v1/auth/login", { email, password });
      router.push(next);
      router.refresh();
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 429) {
        setError(
          `Too many failed attempts. Try again later${err.details && "retry_after" in err.details ? ` (retry after ${String(err.details.retry_after)}s)` : ""}.`,
        );
      } else if (err instanceof ApiError && err.status === 401) {
        setError("Invalid email or password.");
      } else if (err instanceof ApiError) {
        setError(`${err.status} ${err.code}: ${err.message}`);
      } else {
        setError("The server could not be reached. Check that the process is running.");
      }
      setWorking(false);
    }
  };

  return (
    <Card title="Sign in">
      <form className="lw-auth-form" onSubmit={submit}>
        <Input
          label="Email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <Input
          label="Password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          error={error}
        />
        <Button variant="primary" loading={working} type="submit">
          Sign in
        </Button>
      </form>
    </Card>
  );
}

export default function LoginPage() {
  return (
    <div className="lw-auth">
      <div className="lw-auth-brand">
        <span className="lw-brand-mark" aria-hidden="true" />
        <span className="lw-brand-name">laya</span>
        <span className="lw-brand-sub">ops</span>
      </div>
      <Suspense>
        <LoginForm />
      </Suspense>
    </div>
  );
}
