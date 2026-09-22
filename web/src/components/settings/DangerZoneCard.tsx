/* Danger zone (plan task 16): two destructive actions, each behind a confirm
 * dialog that names the object and the consequence (design 4.19).
 * Revoke all sessions calls DELETE /api/v1/sessions (keeps the caller's).
 * Delete trace range previews the count from
 * GET /api/v1/traces?since=&until=&limit=1 then runs the D-013 bulk delete;
 * the audit trace.deleted meta carries the range and count server-side. */
"use client";

import { useEffect, useState } from "react";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import Input from "@/components/ui/Input";
import DisabledReason from "@/components/ui/DisabledReason";
import { useMutate } from "@/lib/mutation";
import { denyReason } from "@/lib/permissions";
import { api } from "@/lib/api";
import type { MeResponse, Paged, TraceDeleteResult, TraceSummary } from "@/lib/api";

function toEpoch(value: string): number | undefined {
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : undefined;
}

export default function DangerZoneCard({ me }: { me: MeResponse | null }) {
  const mutate = useMutate();
  const [revokeOpen, setRevokeOpen] = useState(false);
  const [revokePending, setRevokePending] = useState(false);
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [previewCount, setPreviewCount] = useState<number | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deletePending, setDeletePending] = useState(false);

  const revokeReason = denyReason(me, "session.revoke");
  const deleteReason = denyReason(me, "trace.delete_range");
  const sinceEpoch = toEpoch(since);
  const untilEpoch = toEpoch(until);

  useEffect(() => {
    if (sinceEpoch === undefined && untilEpoch === undefined) {
      setPreviewCount(null);
      setPreviewError(null);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      const query = new URLSearchParams();
      if (sinceEpoch !== undefined) query.set("since", String(sinceEpoch));
      if (untilEpoch !== undefined) query.set("until", String(untilEpoch));
      query.set("limit", "1");
      api
        .get<Paged<TraceSummary>>(`/api/v1/traces?${query.toString()}`, {
          signal: controller.signal,
        })
        .then((page) => {
          setPreviewCount(page.total_estimate);
          setPreviewError(null);
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return;
          setPreviewCount(null);
          setPreviewError(err instanceof Error ? err.message : "Preview failed.");
        });
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [sinceEpoch, untilEpoch]);

  const revokeAll = () => {
    if (revokePending) return;
    setRevokePending(true);
    void mutate<{ revoked: number }>("/api/v1/sessions", {
      method: "del",
      okTitle: "Sessions revoked",
      okBody: (result) => `${result.revoked} sessions ended; yours stays signed in.`,
    }).then(() => {
      setRevokePending(false);
      setRevokeOpen(false);
    });
  };

  const deleteRange = () => {
    if (deletePending) return;
    const query = new URLSearchParams();
    if (sinceEpoch !== undefined) query.set("since", String(sinceEpoch));
    if (untilEpoch !== undefined) query.set("until", String(untilEpoch));
    if (![...query.keys()].length) return;
    setDeletePending(true);
    void mutate<TraceDeleteResult>(`/api/v1/traces?${query.toString()}`, {
      method: "del",
      okTitle: "Traces deleted",
      okBody: (result) => `${result.deleted} traces removed from the range.`,
    }).then((result) => {
      setDeletePending(false);
      setDeleteOpen(false);
      if (result) {
        setSince("");
        setUntil("");
        setPreviewCount(null);
      }
    });
  };

  const openBound = since || until || "the chosen bounds";
  const needsBound = sinceEpoch === undefined && untilEpoch === undefined;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div style={{ display: "grid", gap: 8 }}>
        <strong>Revoke all sessions</strong>
        <span className="lw-hint">
          Signs every user out system-wide except this session. Owner only.
        </span>
        <div>
          <DisabledReason reason={revokeReason}>
            <Button variant="danger" size="sm" onClick={() => setRevokeOpen(true)}>
              Revoke all sessions
            </Button>
          </DisabledReason>
        </div>
      </div>
      <div style={{ display: "grid", gap: 8 }}>
        <strong>Delete trace range</strong>
        <span className="lw-hint">
          Permanently removes every trace (and its observations) between the bounds. Leave a field
          blank for an open bound; at least one is required.
        </span>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
            gap: 12,
          }}
        >
          <Input
            label="Since"
            type="datetime-local"
            value={since}
            onChange={(event) => setSince(event.target.value)}
          />
          <Input
            label="Until"
            type="datetime-local"
            value={until}
            onChange={(event) => setUntil(event.target.value)}
          />
        </div>
        <span className="lw-hint" role="status">
          {previewError
            ? `Preview failed: ${previewError}`
            : previewCount !== null
              ? `About ${previewCount} traces match this range.`
              : "Set a bound to preview the matching trace count."}
        </span>
        <div>
          <DisabledReason reason={deleteReason}>
            <Button
              variant="danger"
              size="sm"
              disabled={needsBound}
              onClick={() => setDeleteOpen(true)}
            >
              Delete range
            </Button>
          </DisabledReason>
        </div>
      </div>

      {revokeOpen ? (
        <Dialog
          open
          title="Revoke all sessions"
          confirmLabel="Revoke"
          onConfirm={revokeAll}
          onClose={() => setRevokeOpen(false)}
        >
          <p>Every signed-in user is signed out immediately.</p>
          <p className="lw-hint">Your own session stays active. The audit log records it.</p>
        </Dialog>
      ) : null}
      {deleteOpen ? (
        <Dialog
          open
          title="Delete trace range"
          confirmLabel="Delete"
          onConfirm={deleteRange}
          onClose={() => setDeleteOpen(false)}
        >
          <p>
            {`Permanently deletes the traces matching ${openBound}` +
              (previewCount !== null ? `, about ${previewCount} rows` : "") +
              ", including their observations and logs."}
          </p>
          <p className="lw-hint">
            This cannot be undone. The audit row trace.deleted records the range and the count.
          </p>
        </Dialog>
      ) : null}
    </div>
  );
}
