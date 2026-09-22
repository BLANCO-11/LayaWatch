/* Section header. Contract: design-language section 4.9.
 * Mono eyebrow plus serif title left, mono meta right, not interactive. */
import "./card.css";

export default function SectionHeader({
  eyebrow,
  title,
  meta,
}: {
  eyebrow: string;
  title: string;
  meta?: React.ReactNode;
}) {
  return (
    <div className="lw-sec-head">
      <span className="lw-sec-title">
        <span className="lw-sr-only">{eyebrow}: </span>
        {title}
      </span>
      {meta ? <span className="lw-sec-meta">{meta}</span> : null}
    </div>
  );
}
