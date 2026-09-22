/* Timeline: icon column 16px, mono timestamp, text row. Section 4.17. */
import "./overlays.css";

export interface TimelineItem {
  ts: string;
  text: React.ReactNode;
  icon?: React.ReactNode;
}

export default function Timeline({ items }: { items: TimelineItem[] }) {
  return (
    <ol className="lw-timeline">
      {items.map((item, i) => (
        <li key={i}>
          <span className="lw-timeline-icon" aria-hidden="true">
            {item.icon ?? (
              <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <circle cx="8" cy="8" r="3" />
              </svg>
            )}
          </span>
          <span className="lw-timeline-ts">{item.ts}</span>
          <span>{item.text}</span>
        </li>
      ))}
    </ol>
  );
}
