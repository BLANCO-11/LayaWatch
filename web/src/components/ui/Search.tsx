/* Search input: type="search" with leading magnifier and clear button. */
"use client";

import { useId } from "react";
import "./form.css";

export default function Search({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  const id = useId();
  return (
    <label className="lw-field" htmlFor={id}>
      <span>{label}</span>
      <span className="lw-search-wrap">
        <svg className="lw-search-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
          <circle cx="7" cy="7" r="4.5" />
          <path d="M10.5 10.5L14 14" strokeLinecap="round" />
        </svg>
        <input
          id={id}
          type="search"
          role="searchbox"
          className="lw-input"
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
        {value ? (
          <button type="button" className="lw-search-clear" onClick={() => onChange("")} aria-label={`Clear ${label}`}>
            ×
          </button>
        ) : null}
      </span>
    </label>
  );
}
