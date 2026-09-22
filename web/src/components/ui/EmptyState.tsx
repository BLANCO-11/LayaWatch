/* Empty, loading and error states. Contract: sections 4.24-4.26.
 * Empty: 20px icon in text-3, serif 16px title, body naming the next action,
 * one secondary button. Skeleton: single shimmer keyframe, blocks mirror the
 * layout. Error: err left border, mono title, status plus code line,
 * copyable request id, Retry button, never a stack trace. */
import Button from "./Button";
import CopyField from "./CopyField";
import "./overlays.css";

export function EmptyIcon() {
  return (
    <svg className="lw-empty-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <rect x="2.5" y="2.5" width="15" height="15" rx="3" />
      <path d="M7 10h6" strokeLinecap="round" />
    </svg>
  );
}

export default function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="lw-empty">
      <EmptyIcon />
      <div className="lw-empty-title">{title}</div>
      <div className="lw-empty-body">{body}</div>
      {action}
    </div>
  );
}

export function Skeleton({ height = 12, width }: { height?: number; width?: string }) {
  return <div className="lw-skeleton" style={{ height, width }} aria-hidden="true" />;
}

export function RouteProgress({ on }: { on: boolean }) {
  return <div className="lw-route-progress" data-on={on} aria-hidden="true" />;
}

export function ErrorState({
  title,
  detail,
  requestId,
  onRetry,
}: {
  title: string;
  detail?: string;
  requestId?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="lw-error" role="alert">
      <div className="lw-error-title">{title}</div>
      {detail ? <div className="lw-error-detail">{detail}</div> : null}
      {requestId ? <CopyField label="Request id" value={requestId} /> : null}
      {onRetry ? (
        <div style={{ marginTop: 10 }}>
          <Button variant="secondary" size="sm" onClick={onRetry}>
            Retry
          </Button>
        </div>
      ) : null}
    </div>
  );
}
