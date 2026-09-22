/* Table. Contract: design-language section 4.11.
 * Real table with th scope=col, aria-sort on sortable time/numeric columns,
 * first-cell link as the focusable row entry. Four states: loading (6 skeleton
 * rows), empty, error with retry, filtered-to-nothing with clear-filters. */
import type { ReactNode } from "react";
import EmptyState, { ErrorState } from "./EmptyState";
import "./table.css";

export interface Column {
  key: string;
  label: string;
  numeric?: boolean;
  sortable?: boolean;
  sortDirection?: "ascending" | "descending" | "none";
  onSort?: () => void;
}

export type TableState = "ready" | "loading" | "empty" | "filtered-empty" | "error";

export default function Table({
  columns,
  children,
  caption,
  state = "ready",
  emptyTitle = "Nothing here yet",
  emptyBody = "No rows match this view.",
  emptyAction,
  onClearFilters,
  errorTitle = "Failed to load rows",
  errorDetail,
  requestId,
  onRetry,
}: {
  columns: Column[];
  children?: ReactNode;
  caption?: string;
  state?: TableState;
  emptyTitle?: string;
  emptyBody?: string;
  emptyAction?: ReactNode;
  onClearFilters?: () => void;
  errorTitle?: string;
  errorDetail?: string;
  requestId?: string;
  onRetry?: () => void;
}) {
  const head = (
    <thead>
      <tr>
        {columns.map((col) => (
          <th
            key={col.key}
            scope="col"
            className={col.numeric ? "num" : undefined}
            aria-sort={col.sortable ? (col.sortDirection ?? "none") : undefined}
          >
            {col.sortable && onSortFor(col) ? (
              <button
                type="button"
                className="lw-tag-x"
                onClick={col.onSort}
                aria-label={`Sort by ${col.label}`}
              >
                {col.label}
                <span className="lw-sort-caret" aria-hidden="true">
                  {col.sortDirection === "ascending"
                    ? " ▲"
                    : col.sortDirection === "descending"
                      ? " ▼"
                      : " ⇅"}
                </span>
              </button>
            ) : (
              col.label
            )}
          </th>
        ))}
      </tr>
    </thead>
  );

  if (state === "loading") {
    return (
      <div className="lw-tbl-wrap">
        <table>
          {caption ? <caption className="lw-sr-only">{caption}</caption> : null}
          {head}
          <tbody className="lw-tbl-skel" aria-hidden="true">
            {Array.from({ length: 6 }, (_, i) => (
              <tr key={i}>
                {columns.map((col) => (
                  <td key={col.key}>
                    <div className="lw-skeleton" style={{ height: 12 }} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        <span className="lw-sr-only" role="status">
          Loading rows
        </span>
      </div>
    );
  }

  if (state === "empty" || state === "filtered-empty") {
    return (
      <div className="lw-tbl-wrap">
        <EmptyState
          title={state === "filtered-empty" ? "No rows match these filters" : emptyTitle}
          body={
            state === "filtered-empty"
              ? "Adjust the filters or clear them to see all rows."
              : emptyBody
          }
          action={
            state === "filtered-empty" && onClearFilters ? (
              <button type="button" className="lw-btn lw-btn-secondary lw-btn-sm" onClick={onClearFilters}>
                Clear filters
              </button>
            ) : (
              emptyAction
            )
          }
        />
      </div>
    );
  }

  if (state === "error") {
    return (
      <div className="lw-tbl-wrap">
        <ErrorState
          title={errorTitle}
          detail={errorDetail}
          requestId={requestId}
          onRetry={onRetry}
        />
      </div>
    );
  }

  return (
    <div className="lw-tbl-wrap">
      <table>
        {caption ? <caption className="lw-sr-only">{caption}</caption> : null}
        {head}
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function onSortFor(col: Column): boolean {
  return Boolean(col.sortable && col.onSort);
}
