/* Trace table: fixed trace columns over the shared Table, plus the status
 * Badge mapping (design language 4.6: the number and a word are mandatory).
 * Row entry is the link in the first cell (4.11). Used by Overview recent
 * traces and the Traces list. */
import Link from "next/link";
import Table, { type Column, type TableState } from "./Table";
import Badge, { type BadgeTone } from "./Badge";
import type { TraceSummary } from "@/lib/api";
import { clockTime } from "@/lib/format";

export function statusTone(status: number): BadgeTone {
  if (status >= 500) return "err";
  if (status >= 400) return "warn";
  return "ok";
}

export function statusLabel(status: number): string {
  if (status >= 500) return `${status} error`;
  if (status >= 400) return `${status} reject`;
  return `${status} OK`;
}

export const TRACE_COLUMNS: Column[] = [
  { key: "ts", label: "Time", sortable: true, sortDirection: "descending" },
  { key: "id", label: "Request id" },
  { key: "route", label: "Route" },
  { key: "status", label: "Status" },
  { key: "model", label: "Model" },
  { key: "duration_ms", label: "Total ms", numeric: true, sortable: true, sortDirection: "descending" },
  { key: "queue_ms", label: "Queue ms", numeric: true },
];

export default function TraceTable({
  items,
  state,
  emptyTitle = "No traces yet",
  emptyBody = "Send a request to /predict to see one.",
  onClearFilters,
  errorDetail,
  requestId,
  onRetry,
  caption = "Traces, newest first",
}: {
  items: TraceSummary[];
  state: TableState;
  emptyTitle?: string;
  emptyBody?: string;
  onClearFilters?: () => void;
  errorDetail?: string;
  requestId?: string;
  onRetry?: () => void;
  caption?: string;
}) {
  return (
    <Table
      columns={TRACE_COLUMNS}
      state={state}
      caption={caption}
      emptyTitle={emptyTitle}
      emptyBody={emptyBody}
      onClearFilters={onClearFilters}
      errorTitle="Failed to load traces"
      errorDetail={errorDetail}
      requestId={requestId}
      onRetry={onRetry}
    >
      {items.map((trace) => (
        <tr key={trace.id}>
          <td className="mono">
            <Link href={`/traces/${trace.id}`} aria-label={`Trace ${trace.id}`}>
              {clockTime(trace.ts_start)}
            </Link>
          </td>
          <td className="lw-id-cell">{trace.id}</td>
          <td className="mono">{trace.route}</td>
          <td>
            <Badge tone={statusTone(trace.status)}>{statusLabel(trace.status)}</Badge>
          </td>
          <td>{trace.model || "none"}</td>
          <td className="num">{trace.duration_ms.toFixed(1)}</td>
          <td className="num">{trace.queue_ms.toFixed(1)}</td>
        </tr>
      ))}
    </Table>
  );
}
