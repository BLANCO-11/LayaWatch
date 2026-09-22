/* Banner and alert. Contract: design-language section 4.22.
 * Flat full-width block with a 3px severity border. One banner at a time,
 * per-session dismissal. */
"use client";

import { useState } from "react";
import "./overlays.css";

export type BannerTone = "info" | "warn" | "err";

export default function Banner({
  tone = "info",
  storageKey,
  children,
}: {
  tone?: BannerTone;
  storageKey?: string;
  children: React.ReactNode;
}) {
  const [dismissed, setDismissed] = useState(
    () => storageKey !== undefined && sessionStorage.getItem(storageKey) === "1",
  );
  if (dismissed) return null;
  const cls = tone === "info" ? "lw-banner" : `lw-banner lw-banner-${tone}`;
  return (
    <div className={cls} role={tone === "info" ? "status" : "alert"}>
      <span>{children}</span>
      <button
        type="button"
        className="lw-tag-x"
        onClick={() => {
          if (storageKey) {
            try {
              sessionStorage.setItem(storageKey, "1");
            } catch {
              /* session-only dismissal when storage is unavailable */
            }
          }
          setDismissed(true);
        }}
        aria-label="Dismiss notice"
      >
        Dismiss
      </button>
    </div>
  );
}
