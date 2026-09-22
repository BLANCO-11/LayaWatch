/* Fixed nav groups. Contract: docs/design-language.md section 3.
 * Observe: Overview, Traces, Metrics, Logs. Operate: Playground, Checkpoints.
 * Admin: API Keys, Users, Settings. */

export interface NavEntry {
  label: string;
  href: string;
  group: string;
}

export interface NavGroup {
  name: string;
  entries: NavEntry[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    name: "Observe",
    entries: [
      { label: "Overview", href: "/", group: "Observe" },
      { label: "Traces", href: "/traces", group: "Observe" },
      { label: "Metrics", href: "/metrics", group: "Observe" },
      { label: "Logs", href: "/logs", group: "Observe" },
    ],
  },
  {
    name: "Operate",
    entries: [
      { label: "Playground", href: "/playground", group: "Operate" },
      { label: "Checkpoints", href: "/checkpoints", group: "Operate" },
    ],
  },
  {
    name: "Admin",
    entries: [
      { label: "API Keys", href: "/keys", group: "Admin" },
      { label: "Users", href: "/users", group: "Admin" },
      { label: "Settings", href: "/settings", group: "Admin" },
    ],
  },
];

/* Active-entry lookup: /traces/abc resolves to the Traces entry. */
export function activeEntry(pathname: string): NavEntry | null {
  if (pathname === "/") return NAV_GROUPS[0].entries[0];
  for (const group of NAV_GROUPS) {
    for (const entry of group.entries) {
      if (entry.href !== "/" && (pathname === entry.href || pathname.startsWith(`${entry.href}/`))) {
        return entry;
      }
    }
  }
  return null;
}
