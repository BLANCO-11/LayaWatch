/* Card and panel. Contract: design-language section 4.8.
 * Containers, not buttons: a clickable card exposes a real link or button
 * inside; whole-card click only when the card has exactly one action. */
import "./card.css";

export type CardVariant = "flat" | "raised" | "inset";

export default function Card({
  variant = "raised",
  title,
  meta,
  footer,
  href,
  children,
}: {
  variant?: CardVariant;
  title?: string;
  meta?: React.ReactNode;
  footer?: React.ReactNode;
  href?: string;
  children: React.ReactNode;
}) {
  const cls = `lw-card lw-card-${variant}${href ? " lw-card-clickable" : ""}`;
  const head =
    title || meta ? (
      <div className="lw-card-head">
        <span className="lw-card-title">{title}</span>
        <span className="lw-card-meta">{meta}</span>
      </div>
    ) : null;
  const body = <div className="lw-card-body">{children}</div>;
  const foot = footer ? <div className="lw-card-foot">{footer}</div> : null;
  if (href) {
    return (
      <a className={cls} href={href}>
        {head}
        {body}
        {foot}
      </a>
    );
  }
  return (
    <div className={cls}>
      {head}
      {body}
      {foot}
    </div>
  );
}
