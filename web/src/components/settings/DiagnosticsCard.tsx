/* Diagnostics card (plan task 15): self-observability counters from
 * GET /api/v1/meta as a key-value list (design 4.27); the page refreshes the
 * meta payload on every SSE pulse. Missing counters render as "-". */
"use client";

import KeyValueList, { type KeyValue } from "@/components/ui/KeyValueList";
import type { MetaResponse } from "@/lib/api";

const COUNTERS = [
  "obs_dropped_total",
  "write_queue_depth",
  "write_latency_ms",
  "db_size_bytes",
  "rss_mb",
  "ring_usage",
  "sse_clients",
  "ratelimit_blocks_total",
  "engine_shed_total",
];

function display(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(1);
  }
  if (typeof value === "boolean") return value ? "on" : "off";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export default function DiagnosticsCard({ meta }: { meta: MetaResponse | null }) {
  const items: KeyValue[] = COUNTERS.map((counter) => ({
    key: counter,
    value: meta ? display(meta[counter]) : "-",
  }));
  return <KeyValueList items={items} />;
}
