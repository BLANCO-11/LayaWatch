/* Area and line charts: hand-built SVG with hover crosshair tooltip,
 * gap breaks, and a visually hidden data table. Section 4.14 + section 5. */
"use client";

import { useId, useState } from "react";
import { CHART_H, CHART_W, GridLines, runsFor, seriesMax, xFor, yFor } from "./Axis";
import Legend, { type LegendEntry } from "./Legend";
import "./charts.css";

export interface ChartSeries {
  key: string;
  label: string;
  color: string;
  values: Array<number | null>;
}

function DataTable({ series, times }: { series: ChartSeries[]; times?: string[] }) {
  return (
    <table className="lw-sr-only">
      <caption>Chart data for the current window</caption>
      <thead>
        <tr>
          <th scope="col">Bucket</th>
          {series.map((s) => (
            <th key={s.key} scope="col">
              {s.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {series[0]?.values.map((_, i) => (
          <tr key={i}>
            <th scope="row">{times?.[i] ?? `bucket ${i + 1}`}</th>
            {series.map((s) => (
              <td key={s.key}>{s.values[i] === null ? "no data" : String(s.values[i])}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function HoverTip({
  index,
  series,
  times,
}: {
  index: number;
  series: ChartSeries[];
  times?: string[];
}) {
  const x = xFor(index, series[0]?.values.length ?? 1);
  const lines = series
    .map((s) => `${s.label}: ${s.values[index] === null ? "no data" : s.values[index]}`)
    .join("  ");
  return (
    <g pointerEvents="none" aria-hidden="true">
      <line x1={x} y1={8} x2={x} y2={CHART_H - 8} stroke="var(--line-strong)" strokeWidth="1" />
      <text x={Math.min(Math.max(x + 6, 4), CHART_W - 220)} y={16} className="lw-chart-tip">
        {times?.[index] ?? `bucket ${index + 1}`} {lines}
      </text>
    </g>
  );
}

function useHidden(series: ChartSeries[]): [Record<string, boolean>, (key: string) => void] {
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const toggle = (key: string) => setHidden((prev) => ({ ...prev, [key]: !prev[key] }));
  void series;
  return [hidden, toggle];
}

export function AreaChart({
  series,
  max,
  unit,
  times,
  summary,
}: {
  series: ChartSeries;
  max?: number;
  unit?: string;
  times?: string[];
  summary: string;
}) {
  const id = useId();
  const [hover, setHover] = useState<number | null>(null);
  const peak = max ?? seriesMax(series.values);
  const runs = runsFor(series.values, peak);
  const latest = [...series.values].reverse().find((v) => v !== null);
  const text = `${summary}. Latest ${latest ?? "no data"}${unit ? ` ${unit}` : ""}, max ${peak}.`;
  return (
    <div>
      <svg
        className="lw-chart-svg"
        viewBox={`0 0 ${CHART_W} ${CHART_H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={text}
        aria-describedby={`${id}-tbl`}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const ratio = (e.clientX - rect.left) / Math.max(rect.width, 1);
          setHover(Math.round(ratio * (series.values.length - 1)));
        }}
      >
        <GridLines />
        {runs.map((run) => (
          <g key={run.key}>
            <polygon
              points={`${xFor(run.start, series.values.length).toFixed(1)},${CHART_H - 10} ${run.points} ${xFor(run.end, series.values.length).toFixed(1)},${CHART_H - 10}`}
              fill={series.color}
              fillOpacity="0.14"
            />
            <polyline
              points={run.points}
              fill="none"
              stroke={series.color}
              strokeWidth="2"
              strokeLinejoin="round"
            />
          </g>
        ))}
        {hover !== null ? <HoverTip index={hover} series={[series]} times={times} /> : null}
      </svg>
      <div id={`${id}-tbl`}>
        <DataTable series={[series]} times={times} />
      </div>
    </div>
  );
}

export function LineChart({
  series,
  max,
  unit,
  times,
  summary,
}: {
  series: ChartSeries[];
  max?: number;
  unit?: string;
  times?: string[];
  summary: string;
}) {
  const id = useId();
  const [hover, setHover] = useState<number | null>(null);
  const [hidden, toggle] = useHidden(series);
  const visible = series.filter((s) => !hidden[s.key]);
  const peak = max ?? series.reduce((m, s) => Math.max(m, seriesMax(s.values)), 0);
  const latest = visible
    .map((s) => `${s.label} ${[...s.values].reverse().find((v) => v !== null) ?? "no data"}`)
    .join(", ");
  const text = `${summary}. ${latest}${unit ? ` ${unit}` : ""}.`;
  const entries: LegendEntry[] = series.map((s) => ({
    key: s.key,
    label: s.label,
    color: s.color,
    hidden: Boolean(hidden[s.key]),
  }));
  return (
    <div>
      <svg
        className="lw-chart-svg"
        viewBox={`0 0 ${CHART_W} ${CHART_H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={text}
        aria-describedby={`${id}-tbl`}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const ratio = (e.clientX - rect.left) / Math.max(rect.width, 1);
          const count = series[0]?.values.length ?? 1;
          setHover(Math.round(ratio * (count - 1)));
        }}
      >
        <GridLines />
        {visible.map((s) =>
          runsFor(s.values, peak).map((run) => (
            <polyline
              key={`${s.key}-${run.key}`}
              points={run.points}
              fill="none"
              stroke={s.color}
              strokeWidth="1.8"
              strokeLinejoin="round"
            />
          )),
        )}
        {hover !== null ? <HoverTip index={hover} series={visible} times={times} /> : null}
      </svg>
      <Legend entries={entries} onToggle={toggle} />
      <div id={`${id}-tbl`}>
        <DataTable series={series} times={times} />
      </div>
    </div>
  );
}

/* Crosshair is rendered inline by AreaChart/LineChart; this helper keeps the
 * plan's file manifest (charts/Crosshair.tsx) pointing at the shared math. */
export function crosshairX(index: number, count: number): number {
  return xFor(index, count);
}

export function crosshairY(value: number, max: number): number {
  return yFor(value, max);
}
