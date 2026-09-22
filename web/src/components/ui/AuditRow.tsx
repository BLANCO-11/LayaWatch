/* Audit row. Contract: design-language section 4.31.
 * Mono timestamp, avatar plus actor, action verb, mono target id, result
 * badge. Read-only, filterable by actor and action, never editable. */
import Badge, { type BadgeTone } from "./Badge";
import { Avatar } from "./Avatar";
import "./overlays.css";

export default function AuditRow({
  ts,
  actor,
  actorName,
  action,
  target,
  result,
  tone = "neutral",
}: {
  ts: string;
  actor: string;
  actorName?: string;
  action: string;
  target?: string;
  result: string;
  tone?: BadgeTone;
}) {
  return (
    <div className="lw-audit">
      <span className="lw-audit-ts">{ts}</span>
      <span className="lw-audit-actor">
        <Avatar email={actor} name={actorName} />
        {actorName ?? actor}
      </span>
      <span className="lw-audit-verb">{action}</span>
      {target ? <span className="lw-audit-target">{target}</span> : null}
      <Badge tone={tone}>{result}</Badge>
    </div>
  );
}
