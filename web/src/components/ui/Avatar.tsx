/* Avatar and user menu. Contract: design-language section 4.30.
 * Avatar: 28px initials circle on raised with accent ring while open.
 * Menu items Account, Theme, Sign out built on Dropdown.
 * Sign out posts POST /api/v1/auth/logout, then routes to /login. */
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Dropdown from "./Dropdown";
import { api } from "@/lib/api";
import type { MeResponse } from "@/lib/api";
import "./overlays.css";

export function initialsFor(email: string, name?: string): string {
  const source = (name ?? email).trim();
  if (!source) return "?";
  const parts = source.split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  const local = source.split("@")[0];
  return local.slice(0, 2).toUpperCase();
}

export function Avatar({ email, name, open }: { email: string; name?: string; open?: boolean }) {
  return (
    <span className="lw-avatar" data-open={open ?? false} aria-hidden="true">
      {initialsFor(email, name)}
    </span>
  );
}

export function UserMenu({ me }: { me: MeResponse }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);

  const signOut = async () => {
    try {
      await api.post("/api/v1/auth/logout");
    } catch {
      /* expired sessions still leave: the cookie is dead either way */
    }
    router.push("/login");
    router.refresh();
  };

  return (
    <Dropdown
      label="Account menu"
      trigger={<Avatar email={me.email} name={me.name} open={open} />}
      items={[
        { key: "account", label: `Account: ${me.email}` },
        { key: "theme", label: "Theme: toggle" },
        { key: "signout", label: "Sign out", danger: true },
      ]}
      onSelect={(key) => {
        setOpen(false);
        if (key === "signout") void signOut();
        else if (key === "theme") {
          const root = document.documentElement;
          const next = root.dataset.theme === "light" ? "dark" : "light";
          root.dataset.theme = next;
          try {
            localStorage.setItem("laya-theme", next);
          } catch {
            /* theme still applies for this session */
          }
        } else if (key === "account") {
          router.push("/settings");
        }
      }}
    />
  );
}

export default Avatar;
