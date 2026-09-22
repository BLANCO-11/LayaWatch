/* Log viewer. Contract: design-language section 4.16.
 * Sunken well, mono 12px, line-height 1.9, horizontal scroll, white-space pre.
 * Severity token and id coloring only, never a whole line. Pause freezes the
 * tail without disconnecting (buffered count shown), Clear empties the local
 * view only (a later server fetch, `fetchKey` bump, redraws the lines), Jump to
 * latest returns to follow mode. Beyond 500 entries only a scroll window is
 * rendered so the DOM stays under 600 line nodes (phase 5 criterion 7). */
"use client";

import { useEffect, useRef, useState } from "react";
import type { LogEntry } from "@/lib/api";
import { isoWithTz } from "@/lib/format";
import Button from "./Button";
import "./overlays.css";

/** Rendered-line ceiling; past this the well virtualizes around the viewport. */
const VIRTUALIZE_AT = 500;
/** Extra rows kept above and below the viewport window. */
const WINDOW_PAD = 60;
const ROW_H = 23;

export function logLevelClass(level: string): string {
  const low = level.toLowerCase();
  if (low === "error" || low === "err") return "lw-log-err";
  if (low === "warn" || low === "warning") return "lw-log-wrn";
  if (low === "info" || low === "ok") return "lw-log-ok";
  return "";
}

export default function LogViewer({
  entries,
  following = true,
  fetchKey = 0,
}: {
  entries: LogEntry[];
  following?: boolean;
  /** Bumped by the page on every server fetch; resets a local Clear. */
  fetchKey?: number;
}) {
  const [paused, setPaused] = useState(false);
  const [local, setLocal] = useState<LogEntry[]>(entries);
  const [buffered, setBuffered] = useState(0);
  const [follow, setFollow] = useState(following);
  const [clearedAt, setClearedAt] = useState<number | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const wellRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setLocal((prev) => {
      if (paused) {
        setBuffered(Math.max(0, entries.length - prev.length));
        return prev;
      }
      return entries;
    });
  }, [entries, paused]);

  /* A server fetch (fetchKey bump) redraws lines even after a local Clear. */
  useEffect(() => {
    setClearedAt(null);
  }, [fetchKey]);

  useEffect(() => {
    if (follow && wellRef.current) {
      wellRef.current.scrollTop = wellRef.current.scrollHeight;
    }
  }, [local, follow]);

  const resume = () => {
    setPaused(false);
    setBuffered(0);
    setLocal(entries);
    setClearedAt(null);
    setFollow(true);
  };

  const clear = () => {
    setClearedAt(Date.now() / 1000);
    setBuffered(0);
    setLocal([]);
  };

  const visible = clearedAt === null ? local : local.filter((e) => e.ts >= clearedAt);
  const virtual = visible.length > VIRTUALIZE_AT;
  const wellH = wellRef.current?.clientHeight ?? 600;
  const first = virtual
    ? Math.max(0, Math.floor(scrollTop / ROW_H) - WINDOW_PAD)
    : 0;
  const last = virtual
    ? Math.min(visible.length, first + Math.ceil(wellH / ROW_H) + WINDOW_PAD * 2)
    : visible.length;
  const slice = virtual ? visible.slice(first, last) : visible;
  const padTop = virtual ? first * ROW_H : 0;
  const padBottom = virtual ? Math.max(0, (visible.length - last) * ROW_H) : 0;

  return (
    <div>
      <div className="lw-log-actions">
        <Button variant="secondary" size="sm" onClick={() => setPaused((p) => !p)}>
          {paused ? `Resume${buffered > 0 ? ` (${buffered} buffered)` : ""}` : "Pause"}
        </Button>
        <Button variant="ghost" size="sm" onClick={clear}>
          Clear
        </Button>
        {!follow ? (
          <Button variant="ghost" size="sm" onClick={() => setFollow(true)}>
            Jump to latest
          </Button>
        ) : null}
        {virtual ? (
          <span className="lw-hint" role="status">
            {visible.length.toLocaleString("en-US")} lines loaded, windowed view
          </span>
        ) : null}
      </div>
      <div
        className="lw-log"
        ref={wellRef}
        role="log"
        aria-label="Log tail"
        onScroll={(e) => {
          const el = e.currentTarget;
          setScrollTop(el.scrollTop);
          const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
          setFollow(atBottom);
        }}
      >
        {slice.length === 0 ? (
          <span className="lw-log-empty">No log lines in this window.</span>
        ) : (
          <>
            {padTop > 0 ? <div style={{ height: padTop }} aria-hidden="true" /> : null}
            {slice.map((entry, i) => (
              <div key={`${entry.ts}-${entry.id ?? i}`} className="lw-log-line">
                <span className="lw-log-ts">{isoWithTz(entry.ts)}</span>{" "}
                <span className={logLevelClass(entry.level)}>{entry.level}</span>{" "}
                {entry.source ? <span>{entry.source}</span> : null}{" "}
                {entry.trace_id ? (
                  <span className="lw-log-rid">request_id={entry.trace_id}</span>
                ) : null}{" "}
                <span>{entry.message}</span>
              </div>
            ))}
            {padBottom > 0 ? <div style={{ height: padBottom }} aria-hidden="true" /> : null}
          </>
        )}
      </div>
      {paused ? (
        <div className="lw-hint" style={{ marginTop: 8 }} role="status">
          Paused{buffered > 0 ? `, ${buffered} lines buffered` : ""}. Tail is frozen; the stream
          stays connected.
        </div>
      ) : null}
      <div className="lw-log-actions" style={{ marginTop: 8 }}>
        <Button variant="ghost" size="sm" onClick={resume}>
          Jump to latest
        </Button>
      </div>
    </div>
  );
}
