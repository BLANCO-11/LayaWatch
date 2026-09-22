/* Chart card chrome: title, current value, legend slot, footer with window
 * and unit. Contract: design-language section 4.14. */
import Card from "@/components/ui/Card";
import "./charts.css";

export default function ChartCard({
  title,
  current,
  footer,
  summary,
  children,
}: {
  title: string;
  current?: string;
  footer?: string;
  summary: string;
  children: React.ReactNode;
}) {
  return (
    <Card title={title} meta={current} footer={footer ? <span className="lw-chart-foot">{footer}</span> : undefined}>
      <div role="img" aria-label={summary}>
        {children}
      </div>
    </Card>
  );
}
