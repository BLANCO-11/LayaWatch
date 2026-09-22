/* Server facts card (settings plan): GET /api/v1/settings `effective` values
 * with the raw config origin and the server note. Read-only; the Retention
 * card edits these values. */
"use client";

import KeyValueList, { type KeyValue } from "@/components/ui/KeyValueList";
import type { SettingsResponse } from "@/lib/api";

function display(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "boolean") return value ? "on" : "off";
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

export default function ServerFacts({ payload }: { payload: SettingsResponse }) {
  const effective = payload.effective;
  const items: KeyValue[] = effective
    ? [
        { key: "retention traces", value: String(effective.retention_traces) },
        { key: "retention days", value: String(effective.retention_days) },
        { key: "log ring size", value: String(effective.log_ring_size) },
        { key: "trace sample", value: String(effective.trace_sample) },
        { key: "stream tick", value: `${effective.stream_tick}s` },
        { key: "payload capture", value: effective.capture_payloads ? "on" : "off" },
        { key: "device", value: display(payload.config.device) },
        { key: "bind", value: display(payload.config.bind) },
        { key: "models", value: display(payload.config.models) },
        { key: "english only", value: display(payload.config.english_only) },
        { key: "log level", value: display(payload.config.log_level) },
        { key: "trust proxy", value: display(payload.config.trust_proxy) },
      ]
    : Object.entries(payload.config).map(([key, value]) => ({ key, value: display(value) }));

  return <KeyValueList items={items} />;
}
