/* Badge: outline pill, mono 11px, mandatory word content. Section 4.6. */
import "./misc.css";

export type BadgeTone = "ok" | "warn" | "err" | "neutral" | "accent";

export default function Badge({ tone, children }: { tone: BadgeTone; children: React.ReactNode }) {
  return <span className={`lw-badge lw-badge-${tone}`}>{children}</span>;
}
