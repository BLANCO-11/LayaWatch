/* Sidebar: brand, fixed nav groups, health footer.
 * Contract: design-language section 3. Active item is a raised surface with
 * accent rail, accent dot and weight 600; exactly one active. */
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAV_GROUPS } from "./nav";
import { useAuth } from "@/components/AuthProvider";
import { can } from "@/lib/permissions";
import "./shell.css";

export default function Sidebar() {
  const pathname = usePathname();
  const { me } = useAuth();
  return (
    <aside className="lw-side">
      <div className="lw-brand">
        <span className="lw-brand-mark" aria-hidden="true" />
        <span className="lw-brand-name">laya</span>
        <span className="lw-brand-sub">ops</span>
      </div>
      <nav className="lw-nav" aria-label="Views">
        {NAV_GROUPS.map((group) => (
          <div key={group.name}>
            <div className="lw-nav-group">{group.name}</div>
            {group.entries
              .filter((entry) => entry.href !== "/users" || can(me, "users.read"))
              .map((entry) => {
                const isActive =
                  entry.href === "/"
                    ? pathname === "/"
                    : pathname === entry.href || pathname.startsWith(`${entry.href}/`);
                return (
                  <Link
                    key={entry.href}
                    href={entry.href}
                    className={`lw-nav-item${isActive ? " lw-active" : ""}`}
                    aria-current={isActive ? "page" : undefined}
                  >
                    <span className="lw-nav-dot" aria-hidden="true" />
                    {entry.label}
                  </Link>
                );
              })}
          </div>
        ))}
      </nav>
      <dl className="lw-side-foot" aria-label="Server health">
        <dt>poll</dt>
        <dd>
          <span className="lw-foot-live">live</span> <span id="lw-foot-tick">3s</span>
        </dd>
        <dt>auth</dt>
        <dd className="lw-foot-ok" id="lw-foot-auth">
          ON
        </dd>
        <dt>uptime</dt>
        <dd id="lw-foot-uptime">--</dd>
        <dt>device</dt>
        <dd id="lw-foot-device">--</dd>
        <dt>ring</dt>
        <dd id="lw-foot-ring">--</dd>
      </dl>
    </aside>
  );
}
