/* Tooltip: mono 11.5px bubble, max 240px, 150ms hover delay, immediate on
 * focus, no interactive content inside. Section 4.23. */
"use client";

import { useId, useRef, useState } from "react";
import type { TimerId } from "@/lib/filters";
import "./overlays.css";

export default function Tooltip({ text, children }: { text: string; children: React.ReactNode }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const timer = useRef<TimerId>(undefined);

  const showDelayed = () => {
    clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setOpen(true), 150);
  };

  return (
    <span
      style={{ position: "relative", display: "inline-block" }}
      onMouseEnter={showDelayed}
      onMouseLeave={() => {
        clearTimeout(timer.current);
        setOpen(false);
      }}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      aria-describedby={open ? id : undefined}
    >
      {children}
      {open ? (
        <span className="lw-tip" role="tooltip" id={id}>
          {text}
        </span>
      ) : null}
    </span>
  );
}
