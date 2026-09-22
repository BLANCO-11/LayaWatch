/* Per-key limit, burst and usage-reset editor (plan task 9):
 * PATCH /api/v1/keys/{id} for the fields, POST /api/v1/ratelimits/reset
 * {subject: key id, scope: "api_key"} for the bucket. An empty field clears
 * the override (null = the global default applies). */
"use client";

import { useEffect, useState } from "react";
import Dialog from "@/components/ui/Dialog";
import Input from "@/components/ui/Input";
import Button from "@/components/ui/Button";
import DisabledReason from "@/components/ui/DisabledReason";
import { useMutate } from "@/lib/mutation";
import type { ApiKeyItem } from "@/lib/api";

/** Empty -> null (clear override); digits -> int; anything else -> undefined. */
function parseLimit(value: string): number | null | undefined {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  if (!/^\d+$/.test(trimmed)) return undefined;
  return Number.parseInt(trimmed, 10);
}

export default function LimitDialog({
  row,
  resetReason,
  open,
  onClose,
  onSaved,
}: {
  row: ApiKeyItem | null;
  resetReason: string | null;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const mutate = useMutate();
  const [limit, setLimit] = useState("");
  const [burst, setBurst] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open || row === null) return;
    setLimit(row.rate_limit_per_min === null ? "" : String(row.rate_limit_per_min));
    setBurst(row.burst === null ? "" : String(row.burst));
    setError(null);
    setBusy(false);
  }, [open, row]);

  const resetUsage = () => {
    if (busy || row === null) return;
    setBusy(true);
    void mutate(`/api/v1/ratelimits/reset`, {
      body: { subject: row.id, scope: "api_key" },
      okTitle: "Usage reset",
      okBody: `${row.name}: the rate-limit bucket was cleared.`,
    }).then(() => setBusy(false));
  };

  const save = () => {
    if (busy || row === null) return;
    const nextLimit = parseLimit(limit);
    const nextBurst = parseLimit(burst);
    if (nextLimit === undefined || nextBurst === undefined) {
      setError("Limits must be a non-negative whole number or empty.");
      return;
    }
    setError(null);
    setBusy(true);
    void (async () => {
      const result = await mutate<{ id: string; rate_limit_per_min: number | null }>(
        `/api/v1/keys/${row.id}`,
        {
          method: "patch",
          body: { rate_limit_per_min: nextLimit, burst: nextBurst },
          okTitle: "Limits updated",
          okBody: `${row.name}: ${nextLimit === null ? "unlimited" : `${nextLimit}/min`}`,
          refetch: onSaved,
        },
      );
      setBusy(false);
      if (result) onClose();
    })();
  };

  return (
    <Dialog
      open={open}
      title={`Limits for ${row?.name ?? "key"}`}
      confirmLabel="Save limits"
      onConfirm={save}
      onClose={onClose}
    >
      <div style={{ display: "grid", gap: 12 }}>
        <Input
          label="Requests per minute"
          inputMode="numeric"
          value={limit}
          onChange={(event) => setLimit(event.target.value)}
          hint="Empty uses the global default (unlimited by default)."
        />
        <Input
          label="Burst"
          inputMode="numeric"
          value={burst}
          onChange={(event) => setBurst(event.target.value)}
          hint="Token bucket capacity; empty lets the limit set the capacity."
        />
        {error ? (
          <span className="lw-error-text" role="alert">
            {error}
          </span>
        ) : null}
        <div>
          <DisabledReason reason={resetReason}>
            <Button variant="ghost" size="sm" disabled={busy} onClick={resetUsage}>
              Reset usage
            </Button>
          </DisabledReason>
          <span className="lw-hint" style={{ marginLeft: 8 }}>
            Clears this key's rate-limit bucket; audited as ratelimit.reset.
          </span>
        </div>
        {busy ? (
          <span className="lw-hint" role="status">
            Saving...
          </span>
        ) : null}
      </div>
    </Dialog>
  );
}
