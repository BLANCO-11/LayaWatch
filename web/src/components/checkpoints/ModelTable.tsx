/* Checkpoint table (plan phase-6 task 7): one row per configured checkpoint
 * with loaded/available badges and the D-014 stats columns - size, load
 * time, requests in the last 24h and p50 - read from the GET
 * /api/v1/models stats block. Nulls are real: a checkpoint without a
 * recorded model.load span or rollup data shows "n/a". */
"use client";

import Table, { type Column, type TableState } from "@/components/ui/Table";
import Badge from "@/components/ui/Badge";
import ModelActions from "./ModelActions";
import { mb, thousands } from "@/lib/format";
import type { ApiError, MeResponse, ModelInfo } from "@/lib/api";

const COLUMNS: Column[] = [
  { key: "model", label: "Checkpoint" },
  { key: "status", label: "Status" },
  { key: "size", label: "Size", numeric: true },
  { key: "load", label: "Load time", numeric: true },
  { key: "requests", label: "Requests (24h)", numeric: true },
  { key: "p50", label: "p50 ms", numeric: true },
  { key: "actions", label: "Actions" },
];

export interface ModelTableProps {
  state: TableState;
  info: ModelInfo | null;
  error?: ApiError | null;
  me: MeResponse | null;
  englishOnly: boolean;
  pendingKey: string | null;
  onRun: (action: "load" | "unload", name: string) => void;
  onRetry: () => void;
}

export default function ModelTable({
  state,
  info,
  error,
  me,
  englishOnly,
  pendingKey,
  onRun,
  onRetry,
}: ModelTableProps) {
  const loaded = info?.loaded ?? [];
  const names = info ? [...new Set([...info.available, ...loaded])] : [];
  const tableState = state === "ready" && names.length === 0 ? "empty" : state;
  const stats = info?.stats ?? {};

  return (
    <Table
      columns={COLUMNS}
      caption="Checkpoints"
      state={tableState}
      emptyTitle="No checkpoints configured"
      emptyBody="Add a checkpoint to config.models to make it loadable here."
      errorTitle="Failed to load model state"
      errorDetail={error?.message}
      requestId={error?.requestId}
      onRetry={onRetry}
    >
      {names.map((name) => {
        const isLoaded = loaded.includes(name);
        const stat = stats[name];
        return (
          <tr key={name}>
            <td>{name}</td>
            <td>
              <Badge tone={isLoaded ? "ok" : "neutral"}>
                {isLoaded ? "loaded" : "available"}
              </Badge>
            </td>
            <td className="num mono">
              {stat?.size_bytes != null ? mb(stat.size_bytes / 1e6) : "n/a"}
            </td>
            <td className="num mono">
              {stat?.load_ms != null ? `${(stat.load_ms / 1000).toFixed(1)} s` : "n/a"}
            </td>
            <td className="num mono">
              {thousands(stat?.requests_24h ?? 0)}
            </td>
            <td className="num mono">
              {stat?.p50_ms != null ? stat.p50_ms.toFixed(1) : "n/a"}
            </td>
            <td>
              <ModelActions
                me={me}
                name={name}
                loaded={isLoaded}
                loadedCount={loaded.length}
                englishOnly={englishOnly}
                pendingKey={pendingKey}
                onRun={onRun}
              />
            </td>
          </tr>
        );
      })}
    </Table>
  );
}
