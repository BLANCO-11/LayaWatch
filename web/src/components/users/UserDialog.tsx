/* User management dialog (plan task 10): invite (create), role change,
 * disable and force password change, each through the mutation helper with a
 * toast and refetch. Field-level reasons surface the security.md 2
 * invariants before the server's 403 can fire. */
"use client";

import { useEffect, useState } from "react";
import Dialog from "@/components/ui/Dialog";
import Input from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import Button from "@/components/ui/Button";
import DisabledReason from "@/components/ui/DisabledReason";
import { useMutate } from "@/lib/mutation";
import { LAST_OWNER, SELF_ROLE, denyReason, isLastEnabledOwner } from "@/lib/permissions";
import type { MeResponse, Role, UserRow } from "@/lib/api";

const ROLES: Role[] = ["viewer", "admin", "owner"];
const MIN_PASSWORD = 12;

export default function UserDialog({
  mode,
  row,
  users,
  me,
  open,
  onClose,
  onSaved,
}: {
  mode: "create" | "manage";
  row: UserRow | null;
  users: UserRow[];
  me: MeResponse | null;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const mutate = useMutate();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [password, setPassword] = useState("");
  const [manageRole, setManageRole] = useState<Role>("viewer");
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setEmail("");
    setName("");
    setRole("viewer");
    setPassword("");
    setManageRole(row?.role ?? "viewer");
    setFormError(null);
    setBusy(false);
  }, [open, row?.role]);

  const writeReason = denyReason(me, "user.write");
  const isSelf = row !== null && me !== null && row.id === me.id;
  const selfRoleReason = isSelf ? SELF_ROLE : null;
  const demoteReason =
    row !== null && manageRole !== row.role && isLastEnabledOwner(row, users)
      ? LAST_OWNER.demote
      : null;
  const roleReason = writeReason ?? selfRoleReason ?? demoteReason;
  const disableReason =
    row !== null && !row.disabled && isLastEnabledOwner(row, users)
      ? (writeReason ?? LAST_OWNER.disable)
      : writeReason;

  const create = () => {
    if (busy) return;
    if (!email.trim() || !name.trim() || !password) {
      setFormError("email, name and password are all required");
      return;
    }
    if (password.length < MIN_PASSWORD) {
      setFormError(`password must be at least ${MIN_PASSWORD} characters`);
      return;
    }
    setFormError(null);
    setBusy(true);
    void mutate<UserRow>("/api/v1/users", {
      body: { email: email.trim(), name: name.trim(), role, password },
      okTitle: "User created",
      okBody: (created) => `${created.email} must change the password at first sign-in.`,
      refetch: onSaved,
    }).then((result) => {
      setBusy(false);
      if (result) onClose();
    });
  };

  const saveRole = () => {
    if (row === null || busy || roleReason !== null || manageRole === row.role) return;
    setBusy(true);
    void mutate<UserRow>(`/api/v1/users/${row.id}`, {
      method: "patch",
      body: { role: manageRole },
      okTitle: "Role changed",
      okBody: `${row.email} is now ${manageRole}; their sessions were revoked.`,
      refetch: onSaved,
    }).then(() => setBusy(false));
  };

  const toggleDisabled = () => {
    if (row === null || busy || disableReason !== null) return;
    const next = !row.disabled;
    setBusy(true);
    void mutate<UserRow>(`/api/v1/users/${row.id}`, {
      method: "patch",
      body: { disabled: next },
      okTitle: next ? "Account disabled" : "Account enabled",
      okBody: next
        ? `${row.email} can no longer sign in; their sessions were revoked.`
        : `${row.email} can sign in again.`,
      refetch: onSaved,
    }).then(() => setBusy(false));
  };

  const forceChange = () => {
    if (row === null || busy || writeReason !== null || row.must_change) return;
    setBusy(true);
    void mutate<UserRow>(`/api/v1/users/${row.id}`, {
      method: "patch",
      body: { must_change: true },
      okTitle: "Password change required",
      okBody: `${row.email} must set a new password at next sign-in.`,
      refetch: onSaved,
    }).then(() => setBusy(false));
  };

  return (
    <Dialog
      open={open}
      title={mode === "create" ? "Invite user" : `Manage ${row?.name || row?.email || "user"}`}
      confirmLabel={mode === "create" ? "Create user" : undefined}
      onConfirm={mode === "create" ? create : undefined}
      onClose={onClose}
    >
      {mode === "create" ? (
        <div style={{ display: "grid", gap: 12 }}>
          <Input
            label="Email"
            type="email"
            placeholder="operator@example.com"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
          <Input
            label="Name"
            placeholder="Ada Operator"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <Select
            label="Role"
            value={role}
            onChange={(event) => setRole(event.target.value as Role)}
            hint="Viewer is read-only; admin manages keys, models and settings."
          >
            {ROLES.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </Select>
          <Input
            label="Initial password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            hint={`At least ${MIN_PASSWORD} characters. The user must change it at first sign-in.`}
          />
          {formError ? (
            <span className="lw-error-text" role="alert">
              {formError}
            </span>
          ) : null}
          {busy ? (
            <span className="lw-hint" role="status">
              Creating user...
            </span>
          ) : null}
        </div>
      ) : row ? (
        <div style={{ display: "grid", gap: 16 }}>
          <span className="lw-hint">{row.email}</span>
          <div style={{ display: "grid", gap: 8 }}>
            <DisabledReason reason={writeReason ?? selfRoleReason}>
              <Select
                label="Role"
                value={manageRole}
                disabled={busy}
                onChange={(event) => setManageRole(event.target.value as Role)}
              >
                {ROLES.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </Select>
            </DisabledReason>
            <div>
              <DisabledReason reason={roleReason}>
                <Button
                  variant="primary"
                  size="sm"
                  disabled={busy || manageRole === row.role}
                  onClick={saveRole}
                >
                  Save role
                </Button>
              </DisabledReason>
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <DisabledReason reason={disableReason}>
              <Button size="sm" disabled={busy} onClick={toggleDisabled}>
                {row.disabled ? "Enable account" : "Disable account"}
              </Button>
            </DisabledReason>
            <DisabledReason reason={writeReason}>
              <Button
                size="sm"
                disabled={busy || row.must_change}
                onClick={forceChange}
              >
                Force password change
              </Button>
            </DisabledReason>
          </div>
          {row.must_change ? (
            <span className="lw-hint">A password change is already required at next sign-in.</span>
          ) : null}
          {busy ? (
            <span className="lw-hint" role="status">
              Saving...
            </span>
          ) : null}
        </div>
      ) : null}
    </Dialog>
  );
}
