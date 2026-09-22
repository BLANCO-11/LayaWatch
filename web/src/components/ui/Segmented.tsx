/* Segmented control. Contract: design-language section 4.5.
 * Inline single-select for 2-5 short options. Raised container, raised active
 * pill, roving tabindex with arrow keys, never used for navigation. */
"use client";

import { useId, useRef, useState } from "react";
import "./misc.css";

export interface SegmentOption {
  value: string;
  label: string;
}

export default function Segmented({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: SegmentOption[];
  value: string;
  onChange: (value: string) => void;
}) {
  const groupId = useId();
  const buttons = useRef<Array<HTMLButtonElement | null>>([]);
  const [focusIndex, setFocusIndex] = useState(() =>
    Math.max(0, options.findIndex((o) => o.value === value)),
  );

  const focusAt = (index: number) => {
    const clamped = (index + options.length) % options.length;
    setFocusIndex(clamped);
    buttons.current[clamped]?.focus();
  };

  return (
    <div className="lw-seg-wrap">
      <span className="lw-eyebrow lw-seg-label" id={groupId}>
        {label}
      </span>
      <div className="lw-seg" role="group" aria-labelledby={groupId}>
        {options.map((opt, i) => (
          <button
            key={opt.value}
            ref={(el) => {
              buttons.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={opt.value === value}
            tabIndex={i === focusIndex ? 0 : -1}
            className={`lw-seg-opt${opt.value === value ? " lw-seg-on" : ""}`}
            onClick={() => {
              setFocusIndex(i);
              onChange(opt.value);
            }}
            onKeyDown={(e) => {
              if (e.key === "ArrowRight" || e.key === "ArrowDown") {
                e.preventDefault();
                focusAt(i + 1);
              } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
                e.preventDefault();
                focusAt(i - 1);
              } else if (e.key === "Home") {
                e.preventDefault();
                focusAt(0);
              } else if (e.key === "End") {
                e.preventDefault();
                focusAt(options.length - 1);
              } else if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                setFocusIndex(i);
                onChange(opt.value);
              }
            }}
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  );
}
