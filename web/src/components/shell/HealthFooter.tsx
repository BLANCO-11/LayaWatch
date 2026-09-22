/* Health footer enrichment: fills the sidebar footer facts from
 * GET /api/v1/meta (poll mode, auth state, uptime, device, ring size).
 * Silent on failure; server facts are a progressive enhancement. */
"use client";

import { useEffect } from "react";
import { api, type MetaResponse } from "@/lib/api";
import { durationShort, thousands } from "@/lib/format";

export default function HealthFooter() {
  useEffect(() => {
    let cancelled = false;
    const set = (id: string, text: string) => {
      const el = document.getElementById(id);
      if (el) el.textContent = text;
    };
    api
      .get<MetaResponse>("/api/v1/meta")
      .then((meta) => {
        if (cancelled) return;
        set("lw-foot-uptime", durationShort(meta.uptime_s));
        set("lw-foot-device", String(meta.config?.device ?? meta.device ?? "--"));
        const ring = meta.config?.ring_traces;
        set("lw-foot-ring", typeof ring === "number" ? thousands(ring) : "--");
        if (typeof meta.auth_enabled === "boolean") {
          set("lw-foot-auth", meta.auth_enabled ? "ON" : "OFF");
        }
        const tick = meta.config?.stream_tick;
        if (typeof tick === "number") set("lw-foot-tick", `${tick}s`);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);
  return null;
}
