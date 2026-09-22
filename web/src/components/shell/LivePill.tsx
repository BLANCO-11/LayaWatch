/* Topbar live pill. Contract: design-language sections 4.32 and 7.
 * Carries text, uses aria-live="polite", reflects SSE state: `live` while
 * connected, `polling 3s` while the fallback poll runs, and the plain status
 * word while connecting, reconnecting or offline. */
"use client";

import type { StreamStatus } from "@/lib/stream";
import { STREAM_STATUS_LABEL } from "@/lib/stream";
import StatusDot, { type DotTone } from "./StatusDot";

const TONE: Record<StreamStatus, DotTone> = {
  connecting: "idle",
  connected: "ok",
  reconnecting: "warn",
  polling: "warn",
  offline: "err",
};

export default function LivePill({ status, tickS }: { status: StreamStatus; tickS?: number }) {
  const text =
    status === "polling"
      ? `polling ${tickS ?? 3}s`
      : STREAM_STATUS_LABEL[status];
  return (
    <span className="lw-live" aria-live="polite" title={`stream: ${text}`}>
      <StatusDot tone={TONE[status]} />
      {text}
    </span>
  );
}
