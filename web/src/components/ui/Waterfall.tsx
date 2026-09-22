/* Waterfall. Contract: design-language section 4.15.
 * Row anatomy (name 110px, start 64px, track 1fr, duration 64px), bar colors
 * standard accent, queue warn, forward s2, error err, 2px minimum width,
 * hover highlight with tooltip, one expanded span at a time, ol semantics
 * with per-row aria-label, every duration printed plus the summary line. */
"use client";

import { useState } from "react";
import "../charts/charts.css";

export interface Span {
  name: string;
  start_ms: number;
  duration_ms: number;
  kind?: "standard" | "queue" | "forward" | "error";
  attrs?: Record<string, unknown>;
}

function barClass(kind: Span["kind"]): string {
  if (kind === "queue") return "lw-wf-bar lw-wf-bar-q";
  if (kind === "forward") return "lw-wf-bar lw-wf-bar-fwd";
  if (kind === "error") return "lw-wf-bar lw-wf-bar-err";
  return "lw-wf-bar";
}

export default function Waterfall({
  spans,
  totalMs,
  summary,
}: {
  spans: Span[];
  totalMs: number;
  summary: string;
}) {
  const [open, setOpen] = useState<number | null>(null);
  const safe = totalMs > 0 ? totalMs : 1;

  return (
    <div>
      <ol className="lw-wf">
        {spans.map((span, i) => {
          const left = Math.min(100, Math.max(0, (span.start_ms / safe) * 100));
          const width = Math.max((span.duration_ms / safe) * 100, span.duration_ms > 0 ? 0.4 : 0);
          return (
            <li key={`${span.name}-${i}`}>
              <button
                type="button"
                className="lw-wf-row"
                aria-expanded={open === i}
                aria-label={`${span.name}, starts at ${span.start_ms.toFixed(1)} milliseconds, duration ${span.duration_ms.toFixed(1)} milliseconds`}
                title={`${span.name}: start ${span.start_ms.toFixed(1)} ms, duration ${span.duration_ms.toFixed(1)} ms`}
                onClick={() => setOpen(open === i ? null : i)}
              >
                <span className="lw-wf-name">{span.name}</span>
                <span className="lw-wf-ms">{span.start_ms.toFixed(1)} ms</span>
                <span className="lw-wf-track" aria-hidden="true">
                  <span className={barClass(span.kind)} style={{ left: `${left}%`, width: `${width}%` }} />
                </span>
                <span className="lw-wf-ms">{span.duration_ms.toFixed(1)} ms</span>
                {open === i && span.attrs ? (
                  <span className="lw-wf-detail">{JSON.stringify(span.attrs, null, 2).slice(0, 2000)}</span>
                ) : null}
              </button>
            </li>
          );
        })}
      </ol>
      <div className="lw-chart-foot">
        <span>{summary}</span>
      </div>
    </div>
  );
}
