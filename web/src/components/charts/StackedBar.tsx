/* Stacked bar: model mix per bucket with a legend naming each model and a
 * total label on hover. Contract: design-language section 5 rule 5. */
"use client";

import { useId, useState } from "react";
import Legend from "./Legend";
import "./charts.css";

export interface MixBucket {
  label: string;
  total: number;
  parts: Array<{ key: string; label: string; color: string; value: number }>;
}

export default function StackedBar({
  buckets,
  summary,
}: {
  buckets: MixBucket[];
  summary: string;
}) {
  const id = useId();
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const [hover, setHover] = useState<number | null>(null);
  const keys = buckets[0]?.parts ?? [];
  const toggle = (key: string) => setHidden((prev) => ({ ...prev, [key]: !prev[key] }));

  const widthFor = (bucket: MixBucket, partKey: string): number => {
    if (bucket.total <= 0) return 0;
    const part = bucket.parts.find((p) => p.key === partKey);
    if (!part || hidden[partKey]) return 0;
    return (part.value / bucket.total) * 100;
  };

  return (
    <div role="img" aria-label={summary} aria-describedby={`${id}-tbl`}>
      <div className="lw-legend" style={{ marginBottom: 14 }} aria-hidden="true">
        {keys.map((k) => (
          <span key={k.key} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <i style={{ background: k.color, width: 10, height: 10, borderRadius: 2 }} />
            {k.label}
          </span>
        ))}
      </div>
      {buckets.map((bucket, i) => (
        <div
          key={bucket.label}
          className="lw-mixrow"
          style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}
          onMouseEnter={() => setHover(i)}
          onMouseLeave={() => setHover(null)}
          title={`${bucket.label}: total ${bucket.total}`}
        >
          <span
            className="lw-bar-lab mono"
            style={{ width: 52, fontSize: 11, color: "var(--text-3)" }}
          >
            {bucket.label}
          </span>
          <div
            className="lw-mixbar"
            style={{
              flex: 1,
              display: "flex",
              height: 14,
              borderRadius: 3,
              overflow: "hidden",
              background: "var(--bg)",
              border: "1px solid var(--line)",
            }}
          >
            {keys.map((k) => (
              <i
                key={k.key}
                style={{ width: `${widthFor(bucket, k.key)}%`, background: k.color }}
                aria-hidden="true"
              />
            ))}
          </div>
          {hover === i ? (
            <span className="mono" style={{ fontSize: 11, color: "var(--text-2)" }}>
              {bucket.total}
            </span>
          ) : null}
        </div>
      ))}
      <Legend
        entries={keys.map((k) => ({
          key: k.key,
          label: k.label,
          color: k.color,
          hidden: Boolean(hidden[k.key]),
        }))}
        onToggle={toggle}
      />
      <table className="lw-sr-only" id={`${id}-tbl`}>
        <caption>Model mix for the current window</caption>
        <thead>
          <tr>
            <th scope="col">Bucket</th>
            {keys.map((k) => (
              <th key={k.key} scope="col">
                {k.label}
              </th>
            ))}
            <th scope="col">Total</th>
          </tr>
        </thead>
        <tbody>
          {buckets.map((b) => (
            <tr key={b.label}>
              <th scope="row">{b.label}</th>
              {keys.map((k) => (
                <td key={k.key}>{b.parts.find((p) => p.key === k.key)?.value ?? 0}</td>
              ))}
              <td>{b.total}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
