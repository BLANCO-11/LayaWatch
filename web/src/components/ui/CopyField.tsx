/* Secret reveal and copy field. Contract: sections 4.28-4.29.
 * SecretReveal: dashed accent border, accent-tinted sunken background, mono
 * secret, Copy button, mandatory shown-once note, never auto-dismisses.
 * CopyField: mono value plus ghost Copy swapping to Copied for 1.5s,
 * copy failure surfaces a toast. */
"use client";

import { useRef, useState } from "react";
import type { TimerId } from "@/lib/filters";
import { useToast } from "./Toast";
import "./overlays.css";

export function SecretReveal({ secret, note = "shown once, store it now" }: { secret: string; note?: string }) {
  const { push } = useToast();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(secret);
      push({ tone: "ok", title: "Copied", body: "The secret is on the clipboard." });
    } catch {
      push({ tone: "err", title: "Copy failed", body: "Select the secret and copy it manually." });
    }
  };
  return (
    <div className="lw-secret">
      <code>{secret}</code>
      <button type="button" className="lw-btn lw-btn-secondary lw-btn-sm" onClick={copy}>
        Copy
      </button>
      <span className="lw-secret-note">{note}</span>
    </div>
  );
}

export default function CopyField({ label, value }: { label: string; value: string }) {
  const { push } = useToast();
  const [copied, setCopied] = useState(false);
  const timer = useRef<TimerId>(undefined);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), 1500);
    } catch {
      push({ tone: "err", title: "Copy failed", body: "Select the value and copy it manually." });
    }
  };

  return (
    <span className="lw-copy">
      <span className="lw-copy-value" aria-label={label}>
        {value}
      </span>
      <button type="button" className="lw-btn lw-btn-ghost lw-btn-sm" onClick={copy}>
        {copied ? "Copied" : "Copy"}
      </button>
    </span>
  );
}
