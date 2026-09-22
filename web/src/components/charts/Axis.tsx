/* Shared SVG helpers: gridlines at 25/50/75% only, no vertical lines. */
import "./charts.css";

export const CHART_W = 600;
export const CHART_H = 170;
export const CHART_PAD = 10;

export function xFor(index: number, count: number): number {
  if (count <= 1) return CHART_W / 2;
  return CHART_PAD + (index * (CHART_W - 2 * CHART_PAD)) / (count - 1);
}

export function yFor(value: number, max: number): number {
  const safe = max > 0 ? max : 1;
  return CHART_H - CHART_PAD - (Math.min(value, safe) / safe) * (CHART_H - 2 * CHART_PAD);
}

export function GridLines() {
  return (
    <g aria-hidden="true">
      {[0.25, 0.5, 0.75].map((f) => {
        const y = CHART_H - CHART_PAD - f * (CHART_H - 2 * CHART_PAD);
        return (
          <line
            key={f}
            x1={CHART_PAD}
            y1={y}
            x2={CHART_W - CHART_PAD}
            y2={y}
            stroke="var(--grid)"
            strokeWidth="1"
          />
        );
      })}
    </g>
  );
}

export function pointsFor(values: Array<number | null>, max: number): string {
  return values
    .map((v, i) => (v === null ? null : `${xFor(i, values.length).toFixed(1)},${yFor(v, max).toFixed(1)}`))
    .filter((p) => p !== null)
    .join(" ");
}

/* Split a nullable series into contiguous runs so gaps render as breaks. */
export function runsFor(
  values: Array<number | null>,
  max: number,
): Array<{ key: number; points: string; start: number; end: number }> {
  const runs: Array<{ key: number; points: string; start: number; end: number }> = [];
  let start = -1;
  for (let i = 0; i <= values.length; i++) {
    const v = i < values.length ? values[i] : null;
    if (v !== null && start < 0) start = i;
    if ((v === null || i === values.length) && start >= 0) {
      const slice = values.slice(start, i) as number[];
      const points = slice
        .map((val, k) => `${xFor(start + k, values.length).toFixed(1)},${yFor(val, max).toFixed(1)}`)
        .join(" ");
      runs.push({ key: start, points, start, end: i - 1 });
      start = -1;
    }
  }
  return runs;
}

export function seriesMax(values: Array<number | null>, floor = 0): number {
  let max = floor;
  for (const v of values) {
    if (v !== null && v > max) max = v;
  }
  return max > 0 ? max : 1;
}
