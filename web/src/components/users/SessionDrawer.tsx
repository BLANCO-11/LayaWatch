/* Per-user session list (plan task 10): GET /api/v1/users/{id}/sessions with
 * the owner-only "Revoke all sessions" action (DELETE /api/v1/sessions,
 * which ends every session in the system except the caller's) behind a
 * confirm dialog naming the consequence. */
"use client";

import { useCallback, useEffect, useState } from "react";
import Dialog, { Drawer } from "@/components/ui/Dialog";
import Button from "@/components/ui/Button";
import DisabledReason from "@/components/ui/DisabledReason";
import EmptyState, { ErrorState, Skeleton } from "@/components/ui/EmptyState";
import { useMutate } from "@/lib/mutation";
import { api, ApiError } from "@/lib/api";
import type { Paged, UserSession } from "@/lib/api";
import { clockTime, isoWithTz } from "@/lib/format";

type LoadState = "loading" | "ready" | "empty" | "error";

export default function SessionDrawer({
  userId,
  userName,
  revokeReason,
  open,
  onClose,
}: {
  userId: string;
  userName: string;
  revokeReason: string | null;
  open: boolean;
  onClose: () => void;
}) {
  const mutate = useMutate();
  const [items, setItems] = useState<UserSession[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);

  const load = useCallback(async () => {
    setState("loading");
    try {
      const page = await api.get<Paged<UserSession>>(`/api/v1/users/${userId}/sessions`);
      setItems(page.items);
      setState(page.items.length > 0 ? "ready" : "empty");
      setLoadError(null);
    } catch (err) {
      setState("error");
      setLoadError(err instanceof ApiError ? err : null);
    }
  }, [userId]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const revokeAll = () => {
    if (pending) return;
    setPending(true);
    void mutate<{ revoked: number }>("/api/v1/sessions", {
      method: "del",
      okTitle: "Sessions revoked",
      okBody: (result) => `${result.revoked} sessions ended; yours stays signed in.`,
      refetch: load,
    }).then(() => {
      setPending(false);
      setConfirming(false);
    });
  };

  return (
    <>
      <Drawer open={open} title={`Sessions - ${userName}`} onClose={onClose}>
        {state === "loading" ? (
          <div aria-busy="true" aria-label="Loading sessions" style={{ display: "grid", gap: 10 }}>
            <Skeleton height={14} width="40%" />
            <Skeleton height={12} width="70%" />
            <Skeleton height={14} width="40%" />
            <Skeleton height={12} width="70%" />
          </div>
        ) : state === "error" ? (
          <ErrorState
            title="Failed to load sessions"
            detail={loadError?.message}
            requestId={loadError?.requestId}
            onRetry={() => void load()}
          />
        ) : state === "empty" ? (
          <EmptyState title="No active sessions" body="This user has no live sessions." />
        ) : (
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {items.map((session, index) => (
              <li
                key={session.id}
                style={{
                  padding: "10px 0",
                  borderTop: index > 0 ? "1px solid var(--line)" : undefined,
                }}
              >
                <div className="mono" title={isoWithTz(session.created_at)}>
                  {`created ${clockTime(session.created_at)}`}
                </div>
                <div className="lw-hint">
                  {session.last_seen === null
                    ? "never seen"
                    : `last seen ${clockTime(session.last_seen)}`}
                </div>
                <div
                  className="lw-hint mono"
                  style={{ overflowWrap: "anywhere" }}
                >{`${session.ip ?? "unknown ip"} - ${session.user_agent ?? "unknown agent"}`}</div>
              </li>
            ))}
          </ul>
        )}
        <div style={{ marginTop: 16, display: "grid", gap: 6 }}>
          <DisabledReason reason={revokeReason}>
            <Button
              variant="danger"
              size="sm"
              disabled={pending}
              onClick={() => setConfirming(true)}
            >
              Revoke all sessions
            </Button>
          </DisabledReason>
          <span className="lw-hint">
            Ends every session in the system except your own.
          </span>
        </div>
      </Drawer>
      {confirming ? (
        <Dialog
          open
          title="Revoke all sessions"
          confirmLabel="Revoke"
          onConfirm={revokeAll}
          onClose={() => setConfirming(false)}
        >
          <p>Every signed-in user is signed out immediately, including the user shown here.</p>
          <p className="lw-hint">
            Your own session stays active. The audit log records session.revoked_all.
          </p>
        </Dialog>
      ) : null}
    </>
  );
}
