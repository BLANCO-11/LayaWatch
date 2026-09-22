/* Rate limit policy card (plan task 12): all nine controls from
 * docs/rate-limiting.md 6.1 over GET/POST /api/v1/ratelimits. Partial
 * updates only send changed fields; the server applies the policy on the
 * next request without a restart and audits ratelimit.updated. */
"use client";

import { useEffect, useState } from "react";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import Switch from "@/components/ui/Switch";
import DisabledReason from "@/components/ui/DisabledReason";
import { useMutate } from "@/lib/mutation";
import { denyReason } from "@/lib/permissions";
import type { MeResponse, RatelimitPolicy } from "@/lib/api";

const WINDOW_OPTIONS = [
  { seconds: 300, label: "5m" },
  { seconds: 900, label: "15m" },
  { seconds: 3600, label: "1h" },
];

const INT_FIELDS = [
  "engine_per_min",
  "ip_per_min",
  "login",
  "mutation_per_min",
  "playground_per_min",
  "engine_max_inflight",
  "engine_queue_max",
] as const;
type IntField = (typeof INT_FIELDS)[number];

export default function RateLimitsCard({
  policy,
  me,
  onSaved,
}: {
  policy: RatelimitPolicy;
  me: MeResponse | null;
  onSaved: () => void;
}) {
  const mutate = useMutate();
  const [enabled, setEnabled] = useState(true);
  const [loopback, setLoopback] = useState(false);
  const [windowS, setWindowS] = useState(900);
  const [ints, setInts] = useState<Record<IntField, string>>({
    engine_per_min: "0",
    ip_per_min: "0",
    login: "0",
    mutation_per_min: "0",
    playground_per_min: "0",
    engine_max_inflight: "0",
    engine_queue_max: "0",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setEnabled(policy.enabled);
    setLoopback(policy.loopback_exempt);
    setWindowS(policy.login_window);
    setInts({
      engine_per_min: String(policy.engine_per_min),
      ip_per_min: String(policy.ip_per_min),
      login: String(policy.login),
      mutation_per_min: String(policy.mutation_per_min),
      playground_per_min: String(policy.playground_per_min),
      engine_max_inflight: String(policy.engine_max_inflight),
      engine_queue_max: String(policy.engine_queue_max),
    });
    setError(null);
  }, [policy]);

  const reason = denyReason(me, "settings.write");

  const setField = (field: IntField, value: string) =>
    setInts((prev) => ({ ...prev, [field]: value }));

  const save = () => {
    if (busy) return;
    const body: Record<string, number | boolean> = {};
    for (const field of INT_FIELDS) {
      const raw = ints[field].trim();
      if (!/^\d+$/.test(raw)) {
        setError(`${field.replace(/_/g, " ")} must be a non-negative whole number.`);
        return;
      }
      const parsed = Number.parseInt(raw, 10);
      if (parsed !== policy[field]) body[field] = parsed;
    }
    if (enabled !== policy.enabled) body.enabled = enabled;
    if (loopback !== policy.loopback_exempt) body.loopback_exempt = loopback;
    if (windowS !== policy.login_window) body.login_window = windowS;
    if (Object.keys(body).length === 0) {
      setError("Nothing changed; the policy is already saved.");
      return;
    }
    setError(null);
    setBusy(true);
    void mutate("/api/v1/ratelimits", {
      body,
      okTitle: "Rate limits saved",
      okBody: "The next request uses the new policy; no restart.",
      refetch: onSaved,
    }).then(() => setBusy(false));
  };

  const unlimitedHint = (field: IntField) =>
    field === "engine_per_min" && ints[field].trim() === "0"
      ? "0 means unlimited."
      : undefined;

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
          gap: 12,
        }}
      >
        <Switch
          label="Rate limiting enabled"
          checked={enabled}
          disabled={busy}
          onChange={(event) => setEnabled(event.target.checked)}
        />
        <Switch
          label="Exempt loopback clients"
          checked={loopback}
          disabled={busy}
          onChange={(event) => setLoopback(event.target.checked)}
        />
        <Input
          label="Engine per key (/min)"
          inputMode="numeric"
          value={ints.engine_per_min}
          disabled={busy}
          onChange={(event) => setField("engine_per_min", event.target.value)}
          hint={unlimitedHint("engine_per_min")}
        />
        <Input
          label="Engine per IP (/min)"
          inputMode="numeric"
          value={ints.ip_per_min}
          disabled={busy}
          onChange={(event) => setField("ip_per_min", event.target.value)}
        />
        <Input
          label="Login attempts"
          inputMode="numeric"
          value={ints.login}
          disabled={busy}
          onChange={(event) => setField("login", event.target.value)}
        />
        <Select
          label="Login window"
          value={String(windowS)}
          disabled={busy}
          onChange={(event) => setWindowS(Number.parseInt(event.target.value, 10))}
        >
          {WINDOW_OPTIONS.map((option) => (
            <option key={option.seconds} value={String(option.seconds)}>
              {option.label}
            </option>
          ))}
        </Select>
        <Input
          label="Mutations per session (/min)"
          inputMode="numeric"
          value={ints.mutation_per_min}
          disabled={busy}
          onChange={(event) => setField("mutation_per_min", event.target.value)}
        />
        <Input
          label="Playground runs (/min)"
          inputMode="numeric"
          value={ints.playground_per_min}
          disabled={busy}
          onChange={(event) => setField("playground_per_min", event.target.value)}
        />
        <Input
          label="Engine inflight cap"
          inputMode="numeric"
          value={ints.engine_max_inflight}
          disabled={busy}
          onChange={(event) => setField("engine_max_inflight", event.target.value)}
        />
        <Input
          label="Engine queue bound"
          inputMode="numeric"
          value={ints.engine_queue_max}
          disabled={busy}
          onChange={(event) => setField("engine_queue_max", event.target.value)}
        />
      </div>
      {error ? (
        <span className="lw-error-text" role="alert">
          {error}
        </span>
      ) : null}
      <div>
        <DisabledReason reason={reason}>
          <Button variant="primary" size="sm" disabled={busy} onClick={save}>
            Save rate limits
          </Button>
        </DisabledReason>
        <span className="lw-hint" style={{ marginLeft: 8 }}>
          Applies on the next request; no restart. Audited as ratelimit.updated.
        </span>
      </div>
    </div>
  );
}
