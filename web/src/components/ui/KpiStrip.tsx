/* KPI cell and strip. Contract: design-language section 4.10.
 * Value plus unit plus window; delta states its comparison, never a bare arrow. */
import "./kpi.css";

export type KpiTone = "plain" | "ok" | "warn" | "err";

export interface KpiCellProps {
  label: string;
  value: string;
  unit?: string;
  delta?: string;
  tone?: KpiTone;
}

export function KpiCell({ label, value, unit, delta, tone = "plain" }: KpiCellProps) {
  return (
    <div className="lw-kpi">
      <div className="lw-kpi-label">{label}</div>
      <div className="lw-kpi-value">
        {value}
        {unit ? <span className="lw-kpi-unit">{unit}</span> : null}
      </div>
      {delta ? (
        <div className={`lw-kpi-delta${tone === "plain" ? "" : ` lw-kpi-delta-${tone}`}`}>{delta}</div>
      ) : null}
    </div>
  );
}

export function KpiStrip({ children }: { children: React.ReactNode }) {
  return <div className="lw-kpis">{children}</div>;
}

export default KpiStrip;
