/* Retention and sampling card (plan task 14): edits retention_traces,
 * retention_days, log_ring_size, trace_sample and stream_tick through
 * POST /api/v1/settings, loading GET `effective`. Before saving it shows the
 * disk estimate from docs/observability-model.md section 10 (45 to 60 MB per
 * 10,000 traces at steady state), labeled an estimate with the formula. */
"use client";

import { useEffect, useState } from "react";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import DisabledReason from "@/components/ui/DisabledReason";
import { useMutate } from "@/lib/mutation";
import { denyReason } from "@/lib/permissions";
import { thousands } from "@/lib/format";
import type { MeResponse, SettingsResponse } from "@/lib/api";

const FIELDS = ["retention_traces", "retention_days", "log_ring_size", "stream_tick"] as const;
type IntField = (typeof FIELDS)[number];

export default function RetentionCard({
  payload,
  me,
  onSaved,
}: {
  payload: SettingsResponse;
  me: MeResponse | null;
  onSaved: () => void;
}) {
  const mutate = useMutate();
  const effective = payload.effective;
  const [traces, setTraces] = useState("");
  const [days, setDays] = useState("");
  const [logRing, setLogRing] = useState("");
  const [sample, setSample] = useState("");
  const [tick, setTick] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!effective) return;
    setTraces(String(effective.retention_traces));
    setDays(String(effective.retention_days));
    setLogRing(String(effective.log_ring_size));
    setSample(String(effective.trace_sample));
    setTick(String(effective.stream_tick));
    setError(null);
  }, [effective]);

  const reason = denyReason(me, "settings.write");

  /* observability-model.md 10: 10,000 traces hold 45 to 60 MB at steady state. */
  const cap = Number.parseInt(traces, 10);
  const estimate =
    Number.isFinite(cap) && cap > 0
      ? {
          low: Math.round((cap * 45) / 10_000),
          high: Math.round((cap * 60) / 10_000),
        }
      : null;

  const parseIntField = (value: string): number | undefined => {
    const parsed = Number.parseInt(value, 10);
    return /^\d+$/.test(value.trim()) && Number.isFinite(parsed) ? parsed : undefined;
  };

  const save = () => {
    if (busy || !effective) return;
    const body: Record<string, number> = {};
    const bounds: Record<IntField, number> = {
      retention_traces: 1,
      retention_days: 1,
      log_ring_size: 100,
      stream_tick: 1,
    };
    const values: Record<IntField, string> = {
      retention_traces: traces,
      retention_days: days,
      log_ring_size: logRing,
      stream_tick: tick,
    };
    for (const field of FIELDS) {
      const parsed = parseIntField(values[field]);
      if (parsed === undefined || parsed < bounds[field]) {
        setError(`${field.replace("_", " ")} must be a whole number of at least ${bounds[field]}.`);
        return;
      }
      if (effective && parsed !== effective[field]) body[field] = parsed;
    }
    const parsedSample = Number.parseFloat(sample);
    if (!Number.isFinite(parsedSample) || parsedSample < 0 || parsedSample > 1) {
      setError("trace sample must be a number between 0 and 1.");
      return;
    }
    if (parsedSample !== effective.trace_sample) body.trace_sample = parsedSample;
    if (Object.keys(body).length === 0) {
      setError("Nothing changed; the values are already saved.");
      return;
    }
    setError(null);
    setBusy(true);
    void mutate<SettingsResponse>("/api/v1/settings", {
      body,
      okTitle: "Settings saved",
      okBody: "Retention and sampling apply on the next sweep; no restart.",
      refetch: onSaved,
    }).then(() => setBusy(false));
  };

  if (!effective) {
    return (
      <p className="lw-hint">
        This server does not report effective settings yet; the editable values appear once
        GET /api/v1/settings carries them.
      </p>
    );
  }

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
          gap: 12,
        }}
      >
        <Input
          label="Retention: traces"
          inputMode="numeric"
          value={traces}
          onChange={(event) => setTraces(event.target.value)}
          hint="Row cap for stored traces."
        />
        <Input
          label="Retention: days"
          inputMode="numeric"
          value={days}
          onChange={(event) => setDays(event.target.value)}
          hint="Age cap; whichever bound hits first wins."
        />
        <Input
          label="Log ring size"
          inputMode="numeric"
          value={logRing}
          onChange={(event) => setLogRing(event.target.value)}
          hint="Stored log rows, minimum 100."
        />
        <Input
          label="Trace sample"
          inputMode="decimal"
          value={sample}
          onChange={(event) => setSample(event.target.value)}
          hint="0 to 1; errors and refusals are always recorded."
        />
        <Input
          label="Stream tick (s)"
          inputMode="numeric"
          value={tick}
          onChange={(event) => setTick(event.target.value)}
          hint="SSE pulse cadence in seconds."
        />
      </div>
      <div style={{ display: "grid", gap: 4 }}>
        <span className="lw-hint">
          {`Estimated steady state: ${estimate ? `~${thousands(estimate.low)} to ${thousands(estimate.high)} MB` : "-"} ` +
            `for ${thousands(Number.isFinite(cap) ? cap : 0)} traces with the ${days} day cap.`}
        </span>
        <span className="lw-hint">
          Estimate only: 45 to 60 MB per 10,000 traces at steady state (observability-model
          section 10).
        </span>
      </div>
      {error ? (
        <span className="lw-error-text" role="alert">
          {error}
        </span>
      ) : null}
      <div>
        <DisabledReason reason={reason}>
          <Button variant="primary" size="sm" disabled={busy} onClick={save}>
            Save retention
          </Button>
        </DisabledReason>
        {busy ? (
          <span className="lw-hint" role="status" style={{ marginLeft: 8 }}>
            Saving...
          </span>
        ) : null}
      </div>
    </div>
  );
}
