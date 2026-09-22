/* Arm and disarm key auth (plan task 9): GET/POST /api/v1/keys/auth.
 * `reason` covers the permission gate and the server rule that arming needs
 * at least one active key (keys.py set_auth_state). */
"use client";

import Badge from "@/components/ui/Badge";
import DisabledReason from "@/components/ui/DisabledReason";
import Switch from "@/components/ui/Switch";

export default function AuthSwitch({
  enabled,
  reason,
  pending,
  onToggle,
}: {
  enabled: boolean;
  reason: string | null;
  pending: boolean;
  onToggle: (next: boolean) => void;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 16,
      }}
    >
      <DisabledReason reason={reason}>
        <Switch
          label="Require key auth on /predict and /route"
          checked={enabled}
          disabled={pending}
          onChange={(event) => onToggle(event.target.checked)}
        />
      </DisabledReason>
      <Badge tone={enabled ? "ok" : "neutral"}>{enabled ? "auth ON" : "auth OFF"}</Badge>
    </div>
  );
}
