/* Create and rotate flows (plan task 9, design 4.28): the plaintext is
 * revealed exactly once inside this dialog through SecretReveal, never in a
 * toast. Rotation states the 5-minute grace window (security.md 3.3). */
"use client";

import { useEffect, useState } from "react";
import Dialog from "@/components/ui/Dialog";
import Input from "@/components/ui/Input";
import { SecretReveal } from "@/components/ui/SecretReveal";
import { useMutate } from "@/lib/mutation";
import type { ApiKeyItem, KeySecret } from "@/lib/api";

export default function KeyDialog({
  mode,
  row,
  open,
  onClose,
  onSaved,
}: {
  mode: "create" | "rotate";
  row: ApiKeyItem | null;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const mutate = useMutate();
  const [name, setName] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [secret, setSecret] = useState<KeySecret | null>(null);

  useEffect(() => {
    if (open) {
      setName("");
      setFormError(null);
      setBusy(false);
      setSecret(null);
    }
  }, [open]);

  const confirm = () => {
    if (busy) return;
    if (mode === "create" && !name.trim()) {
      setFormError("'name' is required");
      return;
    }
    setBusy(true);
    void (async () => {
      const result =
        mode === "create"
          ? await mutate<KeySecret>("/api/v1/keys", {
              body: { name: name.trim() },
              okTitle: "Key created",
              okBody: (created) => `${created.name ?? "The key"} is active.`,
              refetch: onSaved,
            })
          : await mutate<KeySecret>(`/api/v1/keys/${row?.id ?? ""}/rotate`, {
              okTitle: "Key rotated",
              okBody: "The previous secret keeps working for 5 minutes.",
              refetch: onSaved,
            });
      setBusy(false);
      if (result) setSecret(result);
    })();
  };

  return (
    <Dialog
      open={open}
      title={mode === "create" ? "Create key" : `Rotate ${row?.name ?? "key"}`}
      confirmLabel={secret ? "Close" : mode === "create" ? "Create key" : "Rotate key"}
      onConfirm={secret ? onClose : confirm}
      onClose={onClose}
    >
      {secret ? (
        <div style={{ display: "grid", gap: 10 }}>
          <SecretReveal secret={secret.key} />
          <span className="lw-hint">
            {`New prefix ${secret.prefix}`}
            {secret.grace !== undefined
              ? `; the previous secret expires in ${Math.round(secret.grace / 60)} minutes.`
              : "."}
          </span>
        </div>
      ) : mode === "create" ? (
        <div style={{ display: "grid", gap: 12 }}>
          <Input
            label="Key name"
            placeholder="e.g. foundry-worker"
            value={name}
            onChange={(event) => setName(event.target.value)}
            hint="Shown in the key list. The secret itself is shown once, in this dialog."
          />
          {formError ? (
            <span className="lw-error-text" role="alert">
              {formError}
            </span>
          ) : null}
          {busy ? (
            <span className="lw-hint" role="status">
              Creating key...
            </span>
          ) : null}
        </div>
      ) : (
        <div style={{ display: "grid", gap: 8 }}>
          <p>
            {`Rotating ${row?.name ?? "this key"} (${row?.prefix ?? ""}) issues a new secret now.`}
          </p>
          <p className="lw-hint">
            The previous secret keeps working for 5 minutes so clients can roll without downtime.
            {busy ? " Rotating..." : ""}
          </p>
        </div>
      )}
    </Dialog>
  );
}
