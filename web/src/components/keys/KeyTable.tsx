/* Key inventory rows (plan task 9, design 4.11): name, prefix, status badge,
 * request count, last used, the mono Limit column and per-row actions.
 * Revoked rows keep their badge and carry no actions, per the mock baseline. */
import Table, { type Column, type TableState } from "@/components/ui/Table";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import DisabledReason from "@/components/ui/DisabledReason";
import type { ApiKeyItem } from "@/lib/api";
import { clockTime, thousands } from "@/lib/format";

const KEY_COLUMNS: Column[] = [
  { key: "name", label: "Name" },
  { key: "prefix", label: "Key" },
  { key: "status", label: "Status" },
  { key: "requests", label: "Requests", numeric: true },
  { key: "last_used", label: "Last used" },
  { key: "limit", label: "Limit" },
  { key: "actions", label: "Actions", numeric: true },
];

export default function KeyTable({
  items,
  state,
  writeReason,
  pendingId,
  onRotate,
  onLimit,
  onRevoke,
  errorDetail,
  requestId,
  onRetry,
}: {
  items: ApiKeyItem[];
  state: TableState;
  writeReason: string | null;
  pendingId: string | null;
  onRotate: (row: ApiKeyItem) => void;
  onLimit: (row: ApiKeyItem) => void;
  onRevoke: (row: ApiKeyItem) => void;
  errorDetail?: string;
  requestId?: string;
  onRetry?: () => void;
}) {
  return (
    <Table
      columns={KEY_COLUMNS}
      state={state}
      caption="API keys, newest first"
      emptyTitle="No keys yet"
      emptyBody="Create a key to call /predict from an engine client."
      errorTitle="Failed to load keys"
      errorDetail={errorDetail}
      requestId={requestId}
      onRetry={onRetry}
    >
      {items.map((row) => (
        <tr key={row.id}>
          <td>
            <strong>{row.name}</strong>
          </td>
          <td className="mono">{row.prefix}</td>
          <td>
            <Badge tone={row.revoked ? "neutral" : "ok"}>
              {row.revoked ? "revoked" : "active"}
            </Badge>
          </td>
          <td className="num">{thousands(row.request_count)}</td>
          <td className="mono">
            {row.last_used === null ? "never" : clockTime(row.last_used)}
          </td>
          <td className="mono">
            {row.rate_limit_per_min === null ? "unlimited" : `${row.rate_limit_per_min}/min`}
          </td>
          <td className="num">
            {row.revoked ? null : (
              <span style={{ display: "inline-flex", gap: 8 }}>
                <DisabledReason reason={writeReason}>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={pendingId === row.id}
                    onClick={() => onRotate(row)}
                  >
                    Rotate
                  </Button>
                </DisabledReason>
                <DisabledReason reason={writeReason}>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={pendingId === row.id}
                    onClick={() => onLimit(row)}
                  >
                    Limit
                  </Button>
                </DisabledReason>
                <DisabledReason reason={writeReason}>
                  <Button
                    size="sm"
                    variant="danger"
                    disabled={pendingId === row.id}
                    onClick={() => onRevoke(row)}
                  >
                    Revoke
                  </Button>
                </DisabledReason>
              </span>
            )}
          </td>
        </tr>
      ))}
    </Table>
  );
}
