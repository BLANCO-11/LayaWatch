/* Chip and tag. Contract: design-language section 4.7.
 * Sunken 24px key/value chip; tag reuses it with an x affordance
 * removable by keyboard. */
"use client";

import "./misc.css";

export function Chip({ name, value }: { name: string; value: React.ReactNode }) {
  return (
    <span className="lw-chip">
      {name} <b>{value}</b>
    </span>
  );
}

export function Tag({
  label,
  value,
  onRemove,
}: {
  label: string;
  value: string;
  onRemove?: () => void;
}) {
  return (
    <span className="lw-chip">
      {label} <b>{value}</b>
      {onRemove ? (
        <button
          type="button"
          className="lw-tag-x"
          onClick={onRemove}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " " || e.key === "Delete" || e.key === "Backspace") {
              e.preventDefault();
              onRemove();
            }
          }}
          aria-label={`Remove tag ${label} ${value}`}
        >
          ×
        </button>
      ) : null}
    </span>
  );
}

export default Chip;
