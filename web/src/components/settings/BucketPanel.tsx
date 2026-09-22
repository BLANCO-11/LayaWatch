/* Live bucket panel (plan task 13): rows from GET /api/v1/ratelimits/usage
 * (the server returns only used > 0, sorted by usage ratio), refreshed on
 * every SSE pulse by the page. Per-row Reset posts /api/v1/ratelimits/reset
 * and is audited as ratelimit.reset. */
"use client";

import Table, { type Column } from "@/components/ui/Table";
import Button from "@/components/ui/Button";
import DisabledReason from "@/components/ui/DisabledReason";
import { denyReason } from "@/lib/permissions";
import { durationShort, thousands } from "@/lib/format";
import type { ApiError, MeResponse, RatelimitUsageItem } from "@/lib/api";

const COLUMNS: Column[] = [
  { key: "subject", label: "Subject" },
  { key: "scope", label: "Scope" },
  { key: "used", label: "Used / limit", numeric: true },
  { key: "resets", label: "Resets in", numeric: true },
  { key: "actions", label: "Actions", numeric: true },
];

export default function BucketPanel({
  items,
  error,
  me,
  resettingKey,
  onReset,
  onRetry,
}: {
  items: RatelimitUsageItem[];
  error: ApiError | null;
  me: MeResponse | null;
  resettingKey: string | null;
  onReset: (item: RatelimitUsageItem) => void;
  onRetry: () => void;
}) {
  const reason = denyReason(me, "settings.write");
  return (
    <Table
      columns={COLUMNS}
      state={error ? "error" : items.length > 0 ? "ready" : "empty"}
      caption="Rate limit buckets in use"
      emptyTitle="No buckets in use"
      emptyBody="Every subject is under its limit right now."
      errorTitle="Failed to load bucket usage"
      errorDetail={error?.message}
      requestId={error?.requestId}
      onRetry={onRetry}
    >
      {items.map((item) => {
        const key = `${item.scope}:${item.subject}`;
        return (
          <tr key={key}>
            <td className="mono">{item.subject}</td>
            <td>{item.scope}</td>
            <td className="num mono">
              {`${thousands(item.used)} / ${item.limit > 0 ? thousands(item.limit) : "unlimited"}`}
            </td>
            <td className="num mono">{durationShort(item.resets_in)}</td>
            <td className="num">
              <DisabledReason reason={reason}>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={resettingKey === key}
                  onClick={() => onReset(item)}
                >
                  Reset
                </Button>
              </DisabledReason>
            </td>
          </tr>
        );
      })}
    </Table>
  );
}
