/* API keys view (plan task 9): inventory from GET /api/v1/keys, the arm
 * switch from GET /api/v1/keys/auth, and create, rotate, limit and revoke
 * flows through the shared mutation helper. Permission gates come from
 * GET /api/v1/auth/me; the server stays authoritative. */
"use client";

import { useCallback, useEffect, useState } from "react";
import SectionHeader from "@/components/ui/SectionHeader";
import Card from "@/components/ui/Card";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import DisabledReason from "@/components/ui/DisabledReason";
import KeyTable from "@/components/keys/KeyTable";
import KeyDialog from "@/components/keys/KeyDialog";
import LimitDialog from "@/components/keys/LimitDialog";
import AuthSwitch from "@/components/keys/AuthSwitch";
import { useAuth } from "@/components/AuthProvider";
import { denyReason } from "@/lib/permissions";
import { useMutate } from "@/lib/mutation";
import { api, ApiError } from "@/lib/api";
import type { ApiKeyItem, KeyAuthState, Paged } from "@/lib/api";
import type { TableState } from "@/components/ui/Table";

const ARM_NO_KEY = "Arming key auth requires at least one active key";
const AUTH_UNAVAILABLE = "Key auth state could not be loaded";

export default function KeysPage() {
  const { me } = useAuth();
  const mutate = useMutate();
  const [items, setItems] = useState<ApiKeyItem[]>([]);
  const [state, setState] = useState<TableState>("loading");
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [authEnabled, setAuthEnabled] = useState<boolean | null>(null);
  const [armPending, setArmPending] = useState(false);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [keyDialog, setKeyDialog] = useState<{
    mode: "create" | "rotate";
    row: ApiKeyItem | null;
  } | null>(null);
  const [limitId, setLimitId] = useState<string | null>(null);
  const [revokeId, setRevokeId] = useState<string | null>(null);

  const fetchKeys = useCallback(async () => {
    try {
      const page = await api.get<Paged<ApiKeyItem>>("/api/v1/keys");
      setItems(page.items);
      setState(page.items.length > 0 ? "ready" : "empty");
      setLoadError(null);
    } catch (err) {
      setState("error");
      setLoadError(err instanceof ApiError ? err : null);
    }
  }, []);

  const fetchAuth = useCallback(async () => {
    try {
      const auth = await api.get<KeyAuthState>("/api/v1/keys/auth");
      setAuthEnabled(auth.enabled);
    } catch {
      setAuthEnabled(null);
    }
  }, []);

  const refresh = useCallback(() => {
    void fetchKeys();
    void fetchAuth();
  }, [fetchKeys, fetchAuth]);

  useEffect(() => {
    setState("loading");
    refresh();
  }, [refresh]);

  const createReason = denyReason(me, "key.create");
  const writeReason = denyReason(me, "key.rotate");
  const authGate = denyReason(me, "key.auth");
  const activeCount = items.filter((key) => !key.revoked).length;
  const revokedCount = items.length - activeCount;
  const armReason =
    authGate ??
    (authEnabled === null
      ? AUTH_UNAVAILABLE
      : !authEnabled && activeCount === 0
        ? ARM_NO_KEY
        : null);
  const limitRow = items.find((row) => row.id === limitId) ?? null;
  const revokeRow = items.find((row) => row.id === revokeId) ?? null;

  const toggleAuth = (next: boolean) => {
    if (armPending) return;
    setArmPending(true);
    void mutate<KeyAuthState>("/api/v1/keys/auth", {
      body: { enabled: next },
      okTitle: next ? "Key auth armed" : "Key auth disarmed",
      okBody: next
        ? "Engine routes now require an API key."
        : "Engine routes accept unauthenticated calls again.",
      refetch: refresh,
    }).then(() => setArmPending(false));
  };

  const revoke = () => {
    if (revokeRow === null || pendingId !== null) return;
    const row = revokeRow;
    setPendingId(row.id);
    void mutate(`/api/v1/keys/${row.id}`, {
      method: "del",
      okTitle: "Key revoked",
      okBody: `${row.name} (${row.prefix}) no longer authenticates.`,
      refetch: fetchKeys,
    }).then(() => {
      setPendingId(null);
      setRevokeId(null);
    });
  };

  return (
    <section className="lw-sec" aria-label="API keys">
      <SectionHeader
        eyebrow="Admin"
        title="API Keys"
        meta={`${activeCount} active - ${revokedCount} revoked`}
      />
      <Card variant="flat">
        <div style={{ display: "grid", gap: 16 }}>
          <AuthSwitch
            enabled={authEnabled === true}
            reason={armReason}
            pending={armPending}
            onToggle={toggleAuth}
          />
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <DisabledReason reason={createReason}>
              <Button variant="primary" onClick={() => setKeyDialog({ mode: "create", row: null })}>
                Create key
              </Button>
            </DisabledReason>
          </div>
        </div>
      </Card>
      <Card variant="flat">
        <KeyTable
          items={items}
          state={state}
          writeReason={writeReason}
          pendingId={pendingId}
          onRotate={(row) => setKeyDialog({ mode: "rotate", row })}
          onLimit={(row) => setLimitId(row.id)}
          onRevoke={(row) => setRevokeId(row.id)}
          errorDetail={loadError?.message}
          requestId={loadError?.requestId}
          onRetry={refresh}
        />
      </Card>
      {keyDialog ? (
        <KeyDialog
          mode={keyDialog.mode}
          row={keyDialog.row}
          open
          onClose={() => setKeyDialog(null)}
          onSaved={fetchKeys}
        />
      ) : null}
      {limitId ? (
        <LimitDialog
          row={limitRow}
          resetReason={denyReason(me, "settings.write")}
          open
          onClose={() => setLimitId(null)}
          onSaved={fetchKeys}
        />
      ) : null}
      {revokeRow ? (
        <Dialog
          open
          title={`Revoke ${revokeRow.prefix}`}
          confirmLabel="Revoke"
          onConfirm={revoke}
          onClose={() => setRevokeId(null)}
        >
          <p>
            {`Clients using ${revokeRow.name} (${revokeRow.prefix}) stop authenticating ` +
              "immediately."}
          </p>
          <p className="lw-hint">
            The key stays in the list with the revoked badge. This cannot be undone; create a new
            key instead.
          </p>
        </Dialog>
      ) : null}
    </section>
  );
}
