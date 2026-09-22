/* User rows (plan task 10): avatar plus role badge, status badges, login
 * times and the owner-gated actions. Last-owner and self-protection render
 * disabled with the invariant reason; the server 403 stays authoritative. */
import Table, { type Column, type TableState } from "@/components/ui/Table";
import Badge, { type BadgeTone } from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import DisabledReason from "@/components/ui/DisabledReason";
import Avatar from "@/components/ui/Avatar";
import { LAST_OWNER, SELF_DELETE, isLastEnabledOwner } from "@/lib/permissions";
import type { MeResponse, Role, UserRow } from "@/lib/api";
import { clockTime, isoWithTz } from "@/lib/format";

const ROLE_TONE: Record<Role, BadgeTone> = { owner: "accent", admin: "ok", viewer: "neutral" };

const USER_COLUMNS: Column[] = [
  { key: "user", label: "User" },
  { key: "role", label: "Role" },
  { key: "status", label: "Status" },
  { key: "last_login", label: "Last login" },
  { key: "created", label: "Created" },
  { key: "actions", label: "Actions", numeric: true },
];

export default function UserTable({
  items,
  state,
  me,
  writeReason,
  sessionReason,
  pendingId,
  onManage,
  onSessions,
  onDelete,
  errorDetail,
  requestId,
  onRetry,
}: {
  items: UserRow[];
  state: TableState;
  me: MeResponse | null;
  writeReason: string | null;
  sessionReason: string | null;
  pendingId: string | null;
  onManage: (row: UserRow) => void;
  onSessions: (row: UserRow) => void;
  onDelete: (row: UserRow) => void;
  errorDetail?: string;
  requestId?: string;
  onRetry?: () => void;
}) {
  return (
    <Table
      columns={USER_COLUMNS}
      state={state}
      caption="Users, oldest first"
      emptyTitle="No users"
      emptyBody="Invite an owner or admin to manage access."
      errorTitle="Failed to load users"
      errorDetail={errorDetail}
      requestId={requestId}
      onRetry={onRetry}
    >
      {items.map((row) => {
        const isSelf = me !== null && row.id === me.id;
        const deleteReason =
          writeReason ??
          (isSelf ? SELF_DELETE : isLastEnabledOwner(row, items) ? LAST_OWNER.delete : null);
        return (
          <tr key={row.id}>
            <td>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                <Avatar email={row.email} name={row.name} />
                <span>
                  <strong>{row.name}</strong>
                  <br />
                  <span className="lw-hint">{row.email}</span>
                </span>
              </span>
            </td>
            <td>
              <Badge tone={ROLE_TONE[row.role]}>{row.role}</Badge>
            </td>
            <td>
              <span style={{ display: "inline-flex", gap: 8, flexWrap: "wrap" }}>
                <Badge tone={row.disabled ? "neutral" : "ok"}>
                  {row.disabled ? "disabled" : "active"}
                </Badge>
                {row.must_change && !row.disabled ? (
                  <Badge tone="warn">must change password</Badge>
                ) : null}
              </span>
            </td>
            <td
              className="mono"
              title={row.last_login_at ? isoWithTz(row.last_login_at) : undefined}
            >
              {row.last_login_at === null ? "never" : clockTime(row.last_login_at)}
            </td>
            <td className="mono" title={isoWithTz(row.created_at)}>
              {isoWithTz(row.created_at).slice(0, 10)}
            </td>
            <td className="num">
              <span style={{ display: "inline-flex", gap: 8 }}>
                <DisabledReason reason={writeReason}>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={pendingId === row.id}
                    onClick={() => onManage(row)}
                  >
                    Manage
                  </Button>
                </DisabledReason>
                <DisabledReason reason={sessionReason}>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={pendingId === row.id}
                    onClick={() => onSessions(row)}
                  >
                    Sessions
                  </Button>
                </DisabledReason>
                <DisabledReason reason={deleteReason}>
                  <Button
                    size="sm"
                    variant="danger"
                    disabled={pendingId === row.id}
                    onClick={() => onDelete(row)}
                  >
                    Delete
                  </Button>
                </DisabledReason>
              </span>
            </td>
          </tr>
        );
      })}
    </Table>
  );
}
