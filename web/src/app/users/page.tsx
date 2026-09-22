/* Users view (plan task 10): account list with role badges from
 * GET /api/v1/users, owner-gated invite, role change, disable, force
 * password change and delete, plus the per-user session drawer. Viewers get
 * the access-denied state (the nav item is hidden for them too); admins see
 * a read-only table with owner actions disabled and explained. */
"use client";

import { useCallback, useEffect, useState } from "react";
import SectionHeader from "@/components/ui/SectionHeader";
import Card from "@/components/ui/Card";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import DisabledReason from "@/components/ui/DisabledReason";
import EmptyState from "@/components/ui/EmptyState";
import UserTable from "@/components/users/UserTable";
import UserDialog from "@/components/users/UserDialog";
import SessionDrawer from "@/components/users/SessionDrawer";
import { useAuth } from "@/components/AuthProvider";
import { can, denyReason } from "@/lib/permissions";
import { useMutate } from "@/lib/mutation";
import { api, ApiError } from "@/lib/api";
import type { Paged, UserRow } from "@/lib/api";
import type { TableState } from "@/components/ui/Table";

export default function UsersPage() {
  const { me } = useAuth();
  const mutate = useMutate();
  const [items, setItems] = useState<UserRow[]>([]);
  const [state, setState] = useState<TableState>("loading");
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [manageId, setManageId] = useState<string | null>(null);
  const [sessionsId, setSessionsId] = useState<string | null>(null);
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const fetchUsers = useCallback(async () => {
    try {
      const page = await api.get<Paged<UserRow>>("/api/v1/users");
      setItems(page.items);
      setState(page.items.length > 0 ? "ready" : "empty");
      setLoadError(null);
    } catch (err) {
      setState("error");
      setLoadError(err instanceof ApiError ? err : null);
    }
  }, []);

  const canRead = can(me, "users.read");

  useEffect(() => {
    if (!canRead) return;
    setState("loading");
    void fetchUsers();
  }, [canRead, fetchUsers]);

  const writeReason = denyReason(me, "user.write");
  const manageRow = items.find((row) => row.id === manageId) ?? null;
  const sessionsRow = items.find((row) => row.id === sessionsId) ?? null;
  const deleteRow = items.find((row) => row.id === deleteId) ?? null;

  const remove = () => {
    if (deleteRow === null || pendingId !== null) return;
    const row = deleteRow;
    setPendingId(row.id);
    void mutate(`/api/v1/users/${row.id}`, {
      method: "del",
      okTitle: "User deleted",
      okBody: `${row.email} no longer has access.`,
      refetch: fetchUsers,
    }).then(() => {
      setPendingId(null);
      setDeleteId(null);
    });
  };

  if (!me) {
    return (
      <section className="lw-sec" aria-label="Users">
        <SectionHeader eyebrow="Admin" title="Users" meta="checking session" />
        <Card variant="flat" aria-busy="true">
          <span className="lw-sr-only" role="status">
            Checking session
          </span>
          <div className="lw-skeleton" style={{ height: 14, width: "60%" }} />
        </Card>
      </section>
    );
  }

  if (!canRead) {
    return (
      <section className="lw-sec" aria-label="Users">
        <SectionHeader eyebrow="Admin" title="Users" meta="access denied" />
        <Card variant="flat">
          <EmptyState
            title="Access denied"
            body={
              "The Users view needs the admin role. Viewer accounts are read-only;" +
              " ask an owner for access."
            }
          />
        </Card>
      </section>
    );
  }

  return (
    <section className="lw-sec" aria-label="Users">
      <SectionHeader
        eyebrow="Admin"
        title="Users"
        meta={`${items.length} accounts - ${items.filter((row) => row.disabled).length} disabled`}
      />
      <Card variant="flat">
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <DisabledReason reason={writeReason}>
            <Button variant="primary" onClick={() => setCreateOpen(true)}>
              Invite user
            </Button>
          </DisabledReason>
        </div>
      </Card>
      <Card variant="flat">
        <UserTable
          items={items}
          state={state}
          me={me}
          writeReason={writeReason}
          sessionReason={denyReason(me, "session.read")}
          pendingId={pendingId}
          onManage={(row) => setManageId(row.id)}
          onSessions={(row) => setSessionsId(row.id)}
          onDelete={(row) => setDeleteId(row.id)}
          errorDetail={loadError?.message}
          requestId={loadError?.requestId}
          onRetry={fetchUsers}
        />
      </Card>
      {createOpen ? (
        <UserDialog
          mode="create"
          row={null}
          users={items}
          me={me}
          open
          onClose={() => setCreateOpen(false)}
          onSaved={fetchUsers}
        />
      ) : null}
      {manageId && manageRow ? (
        <UserDialog
          mode="manage"
          row={manageRow}
          users={items}
          me={me}
          open
          onClose={() => setManageId(null)}
          onSaved={fetchUsers}
        />
      ) : null}
      {sessionsRow ? (
        <SessionDrawer
          userId={sessionsRow.id}
          userName={sessionsRow.name || sessionsRow.email}
          revokeReason={denyReason(me, "session.revoke")}
          open
          onClose={() => setSessionsId(null)}
        />
      ) : null}
      {deleteRow ? (
        <Dialog
          open
          title={`Delete ${deleteRow.name}`}
          confirmLabel="Delete"
          onConfirm={remove}
          onClose={() => setDeleteId(null)}
        >
          <p>
            {`${deleteRow.email} is removed permanently and loses all access to this console.`}
          </p>
          <p className="lw-hint">
            The server refuses to delete the last enabled owner or your own account.
          </p>
        </Dialog>
      ) : null}
    </section>
  );
}
