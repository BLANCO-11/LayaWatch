/* Legend chips toggle series visibility. Instant, no reorder. Section 4.14. */
"use client";

import "./charts.css";

export interface LegendEntry {
  key: string;
  label: string;
  color: string;
  hidden: boolean;
}

export default function Legend({
  entries,
  onToggle,
}: {
  entries: LegendEntry[];
  onToggle: (key: string) => void;
}) {
  return (
    <div className="lw-legend" role="group" aria-label="Toggle series">
      {entries.map((entry) => (
        <button
          key={entry.key}
          type="button"
          className="lw-legend-chip"
          aria-pressed={!entry.hidden}
          onClick={() => onToggle(entry.key)}
        >
          <i style={{ background: entry.color }} aria-hidden="true" />
          {entry.label}
        </button>
      ))}
    </div>
  );
}
