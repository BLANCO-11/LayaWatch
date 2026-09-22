/* Load/unload actions for one checkpoint (plan phase-6 task 8).
 * Both buttons sit behind a confirm dialog that names the checkpoint, take a
 * single in-flight guard from the page (`pendingKey`), carry the
 * LAYA_ENGLISH_ONLY reason plus the matching warn-banner copy, and never let
 * the last loaded checkpoint be unloaded. Server 409/503 answers surface
 * through the shared mutation toast. */
"use client";

import { useState } from "react";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import DisabledReason from "@/components/ui/DisabledReason";
import type { MeResponse } from "@/lib/api";
import { denyReason } from "@/lib/permissions";

export const ENGLISH_ONLY_REASON = "Multilingual cannot be loaded while LAYA_ENGLISH_ONLY=1";
const LAST_LOADED_REASON = "The last loaded checkpoint cannot be unloaded";

export interface ModelActionsProps {
  me: MeResponse | null;
  name: string;
  loaded: boolean;
  loadedCount: number;
  englishOnly: boolean;
  /** `action:name` of the in-flight request, or null when idle. */
  pendingKey: string | null;
  /** Runs the mutation; the dialog closes without waiting for the response. */
  onRun: (action: "load" | "unload", name: string) => void;
}

export default function ModelActions({
  me,
  name,
  loaded,
  loadedCount,
  englishOnly,
  pendingKey,
  onRun,
}: ModelActionsProps) {
  const [dialog, setDialog] = useState<"load" | "unload" | null>(null);
  const busy = pendingKey !== null;
  const roleReason = denyReason(me, "model.load");

  const loadReason =
    roleReason ?? (englishOnly && name !== "english" ? ENGLISH_ONLY_REASON : null);
  const unloadReason = roleReason
    ?? (!loaded
      ? "Only a loaded checkpoint can be unloaded"
      : loadedCount <= 1
        ? LAST_LOADED_REASON
        : null);
  const loadPending = pendingKey === `load:${name}`;
  const unloadPending = pendingKey === `unload:${name}`;

  const confirm = (action: "load" | "unload") => {
    setDialog(null);
    onRun(action, name);
  };

  return (
    <>
      <span style={{ display: "inline-flex", gap: 8 }}>
        <DisabledReason reason={loadReason}>
          <Button
            size="sm"
            variant="secondary"
            disabled={busy}
            loading={loadPending}
            onClick={() => setDialog("load")}
          >
            Load
          </Button>
        </DisabledReason>
        <DisabledReason reason={unloadReason}>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            loading={unloadPending}
            onClick={() => setDialog("unload")}
          >
            Unload
          </Button>
        </DisabledReason>
      </span>

      <Dialog
        open={dialog === "load"}
        title={`Load "${name}"?`}
        confirmLabel="Load"
        onConfirm={() => confirm("load")}
        onClose={() => setDialog(null)}
      >
        Loads the {name} checkpoint into the engine. A load is refused with a
        409 while another load is in flight, or with 503 when there is not
        enough free memory.
      </Dialog>
      <Dialog
        open={dialog === "unload"}
        title={`Unload "${name}"?`}
        confirmLabel="Unload"
        onConfirm={() => confirm("unload")}
        onClose={() => setDialog(null)}
      >
        Frees the {name} checkpoint&rsquo;s memory. The next request for it
        pays the load cost again; the last loaded checkpoint cannot be
        unloaded.
      </Dialog>
    </>
  );
}
