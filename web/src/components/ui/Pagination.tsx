/* Pagination: Load older plus mono range label. Section 4.12.
 * Cursor semantics in props, no page numbers, no infinite scroll. */
import Button from "./Button";
import "./table.css";

export default function Pagination({
  rangeLabel,
  hasMore,
  onLoadOlder,
  loading = false,
}: {
  rangeLabel: string;
  hasMore: boolean;
  onLoadOlder: () => void;
  loading?: boolean;
}) {
  return (
    <div className="lw-pager">
      {hasMore ? (
        <Button variant="secondary" size="sm" loading={loading} onClick={onLoadOlder}>
          Load older
        </Button>
      ) : null}
      <span className="lw-range-label">{rangeLabel}</span>
    </div>
  );
}
