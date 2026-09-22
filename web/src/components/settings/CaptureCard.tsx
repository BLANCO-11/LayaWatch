/* Payload capture card (plan task 14): owner-only switch over
 * POST /api/v1/settings {capture_payloads} (the server audits
 * payload_capture.toggled). A dismissible warn banner states the privacy
 * consequence before the first enable. */
"use client";

import { useState } from "react";
import Banner from "@/components/ui/Banner";
import DisabledReason from "@/components/ui/DisabledReason";
import Switch from "@/components/ui/Switch";
import { useMutate } from "@/lib/mutation";
import { denyReason } from "@/lib/permissions";
import type { MeResponse, SettingsResponse } from "@/lib/api";

export default function CaptureCard({
  payload,
  me,
  onSaved,
}: {
  payload: SettingsResponse;
  me: MeResponse | null;
  onSaved: () => void;
}) {
  const mutate = useMutate();
  const [pending, setPending] = useState(false);
  const reason = denyReason(me, "capture.toggle");
  const enabled = payload.effective?.capture_payloads ?? false;

  const toggle = (next: boolean) => {
    if (pending) return;
    setPending(true);
    void mutate<SettingsResponse>("/api/v1/settings", {
      body: { capture_payloads: next },
      okTitle: next ? "Payload capture on" : "Payload capture off",
      okBody: next
        ? "Truncated state and answer payloads are stored in SQLite."
        : "No new payloads are stored.",
      refetch: onSaved,
    }).then(() => setPending(false));
  };

  return (
    <div style={{ display: "grid", gap: 12 }}>
      {!enabled ? (
        <Banner tone="warn" storageKey="lw-capture-privacy">
          Payload capture stores truncated state and answer payloads in SQLite, off by default
          (D-005). Enable only when you accept that privacy trade.
        </Banner>
      ) : null}
      <DisabledReason reason={reason}>
        <Switch
          label="Capture truncated payloads on stored traces"
          checked={enabled}
          disabled={pending}
          onChange={(event) => toggle(event.target.checked)}
        />
      </DisabledReason>
      <span className="lw-hint">
        Redaction runs before truncation; captured payloads are pruned after 24 hours regardless of
        trace retention. Owner only.
      </span>
    </div>
  );
}
