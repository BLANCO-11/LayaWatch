/* First-run owner wizard. Posts POST /api/v1/auth/setup, then routes to
 * /login; renders the closed-setup error state with a link to /login when
 * the endpoint refuses. */
"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { Input } from "@/components/ui/Input";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import "../auth.css";

export default function SetupPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | undefined>(undefined);
  const [closed, setClosed] = useState(false);
  const [working, setWorking] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setWorking(true);
    setError(undefined);
    try {
      await api.post("/api/v1/auth/setup", { email, name, password });
      router.push("/login");
    } catch (err: unknown) {
      if (err instanceof ApiError && (err.status === 403 || err.status === 404)) {
        setClosed(true);
      } else if (err instanceof ApiError) {
        setError(`${err.status} ${err.code}: ${err.message}`);
      } else {
        setError("The server could not be reached. Check that the process is running.");
      }
      setWorking(false);
    }
  };

  if (closed) {
    return (
      <div className="lw-auth">
        <Card title="Setup closed">
          <p className="lw-hint">
            An owner already exists. Setup runs exactly once.
          </p>
          <p style={{ marginTop: 12 }}>
            <Link href="/login">Go to sign in</Link>
          </p>
        </Card>
      </div>
    );
  }

  return (
    <div className="lw-auth">
      <div className="lw-auth-brand">
        <span className="lw-brand-mark" aria-hidden="true" />
        <span className="lw-brand-name">LayaWatch</span>
      </div>
      <Card title="Create the owner account">
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
            label="Name"
            type="text"
            autoComplete="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Input
            label="Password"
            type="password"
            autoComplete="new-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            error={error}
            hint={error ? undefined : "Choose a strong password for the owner account."}
          />
          <Button variant="primary" loading={working} type="submit">
            Create owner
          </Button>
        </form>
      </Card>
    </div>
  );
}
