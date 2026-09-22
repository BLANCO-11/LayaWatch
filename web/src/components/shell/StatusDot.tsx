/* 6px status dot. Contract: design-language section 4.32.
 * Always adjacent to a word; a bare dot is only allowed inside LivePill text. */
import "./shell.css";

export type DotTone = "ok" | "warn" | "err" | "idle";

export default function StatusDot({ tone, label }: { tone: DotTone; label?: string }) {
  return (
    <span className="lw-dot-wrap">
      <span className={`lw-dot lw-dot-${tone}`} aria-hidden="true" />
      {label ? <span>{label}</span> : null}
    </span>
  );
}
