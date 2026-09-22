/* Legacy shim counters (plan task 15): GET /api/v1/meta `deprecation` map.
 * They tell the operator whether anything still depends on the section 14
 * shims before they are removed (D-006). */
"use client";

import EmptyState from "@/components/ui/EmptyState";
import KeyValueList, { type KeyValue } from "@/components/ui/KeyValueList";
import type { MetaResponse } from "@/lib/api";

export default function ShimCountersCard({ meta }: { meta: MetaResponse | null }) {
  const entries = Object.entries(meta?.deprecation ?? {});
  if (entries.length === 0) {
    return (
      <EmptyState
        title="No shim traffic"
        body="Nothing has called a legacy /admin endpoint since the process started."
      />
    );
  }
  const items: KeyValue[] = entries.map(([endpoint, count]) => ({
    key: endpoint,
    value: String(count),
  }));
  return <KeyValueList items={items} />;
}
