/* App shell: skip link, sidebar, topbar, content slot.
 * Contract: design-language section 3 and responsive matrix 1.6.
 * Owns the SSE connection; the live pill renders its state.
 * /login and /setup render bare (no sidebar/topbar): they must work
 * with no session and no stream. */
"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";
import Sidebar from "./Sidebar";
import Topbar from "./Topbar";
import HealthFooter from "./HealthFooter";
import SkipLink from "./SkipLink";
import { useStream } from "@/lib/stream";
import "./shell.css";

const BARE_ROUTES = new Set(["/login", "/setup"]);

export default function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [tickS] = useState(3);
  const status = useStream(
    {
      onPulse: () => {
        const el = document.getElementById("lw-foot-tick");
        if (el && tickS) el.textContent = `${tickS}s`;
      },
    },
    !BARE_ROUTES.has(pathname),
  );
  if (BARE_ROUTES.has(pathname)) {
    return (
      <div className="lw-app">
        <SkipLink />
        <main className="lw-content" id="lw-content" tabIndex={-1}>
          {children}
        </main>
      </div>
    );
  }
  return (
    <div className="lw-app">
      <SkipLink />
      <Sidebar />
      <div className="lw-main">
        <Topbar streamStatus={status} />
        <main className="lw-content" id="lw-content" tabIndex={-1}>
          {children}
        </main>
      </div>
      <HealthFooter />
    </div>
  );
}
