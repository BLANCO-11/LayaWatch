/* Topbar: mono eyebrow plus serif title, live pill, theme toggle.
 * Eyebrow and title derive from the nav entry so exactly one item is active. */
"use client";

import { usePathname } from "next/navigation";
import { activeEntry } from "./nav";
import LivePill from "./LivePill";
import ThemeToggle from "./ThemeToggle";
import { UserMenu } from "@/components/ui/Avatar";
import { useAuth } from "@/components/AuthProvider";
import type { StreamStatus } from "@/lib/stream";
import "./shell.css";

export default function Topbar({ streamStatus }: { streamStatus: StreamStatus }) {
  const pathname = usePathname();
  const entry = activeEntry(pathname);
  const { me } = useAuth();
  return (
    <header className="lw-topbar">
      <div className="lw-tb-left">
        <span className="lw-eyebrow lw-tb-eyebrow">{entry?.group ?? ""}</span>
        <h1 className="lw-tb-title">{entry?.label ?? ""}</h1>
      </div>
      <div className="lw-tb-right">
        <LivePill status={streamStatus} tickS={3} />
        <ThemeToggle />
        {me ? <UserMenu me={me} /> : null}
      </div>
    </header>
  );
}
