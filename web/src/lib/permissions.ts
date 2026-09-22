/* Role and permission layer driven by GET /api/v1/auth/me (plan task 1,
 * docs/security.md section 2). The server table (layawatch/auth/users.py
 * PERMISSIONS) stays authoritative; this map is UX only: it pre-disables
 * controls and names the reason. Every deny reason mirrors a real server
 * outcome (403 or invariant), so a race that slips through still shows the
 * server's message from the toast. */
import type { MeResponse, UserRow } from "@/lib/api";

export type Action =
  | "playground.run"
  | "model.load"
  | "key.create"
  | "key.rotate"
  | "key.revoke"
  | "key.auth"
  | "key.limit"
  | "users.read"
  | "user.write"
  | "session.read"
  | "session.revoke"
  | "audit.read"
  | "settings.write"
  | "capture.toggle"
  | "trace.delete_range";

/** Server permission each action needs (PERMISSIONS keys in auth/users.py). */
const ACTION_PERMISSION: Record<Action, string> = {
  "playground.run": "playground.run",
  "model.load": "models.write",
  "key.create": "keys.write",
  "key.rotate": "keys.write",
  "key.revoke": "keys.write",
  "key.auth": "keys.write",
  "key.limit": "keys.write",
  "users.read": "users.read",
  "user.write": "users.write",
  "session.read": "sessions.read",
  "session.revoke": "sessions.revoke_all",
  "audit.read": "audit.read",
  "settings.write": "settings.write",
  "capture.toggle": "settings.capture",
  "trace.delete_range": "traces.delete",
};

/** Owner-only actions get a named reason; everything else falls back to read-only. */
const OWNER_REASON: Partial<Record<Action, string>> = {
  "user.write": "Only the owner can manage users",
  "session.read": "Only the owner can view sessions",
  "session.revoke": "Only the owner can revoke sessions",
  "capture.toggle": "Only the owner can change payload capture",
};

const READ_ONLY_REASON = "Viewer role is read-only";
const NO_SESSION_REASON = "Your session has not loaded yet";

/** True when the signed-in role holds the permission behind `action`. */
export function can(me: MeResponse | null, action: Action): boolean {
  const permissions = me?.permissions;
  if (!me || !permissions) return false;
  return permissions.includes(ACTION_PERMISSION[action]);
}

/** The reason to render in a tooltip; null when the action is allowed. */
export function denyReason(me: MeResponse | null, action: Action): string | null {
  if (can(me, action)) return null;
  if (!me) return NO_SESSION_REASON;
  if (me.role === "viewer") return READ_ONLY_REASON;
  return OWNER_REASON[action] ?? READ_ONLY_REASON;
}

/* security.md 2 invariants, surfaced before the server 403 can fire.
 * The per-operation texts follow plan task 10. */
export const LAST_OWNER = {
  demote: "The last owner cannot be demoted",
  disable: "The last owner cannot be disabled",
  delete: "The last owner cannot be deleted",
} as const;

export const SELF_ROLE = "Users cannot change their own role";
export const SELF_DELETE = "Users cannot delete themselves";

/** True when `row` is the only enabled owner: demote, disable and delete must
 * all be pre-disabled for it (security.md 2 invariant 1). */
export function isLastEnabledOwner(row: UserRow, users: UserRow[]): boolean {
  if (row.role !== "owner" || row.disabled) return false;
  return !users.some(
    (other) => other.id !== row.id && other.role === "owner" && !other.disabled,
  );
}
