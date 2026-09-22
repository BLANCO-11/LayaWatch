/* Select: native select styled to tokens. Contract: section 4.3. */
"use client";

import { useId, type SelectHTMLAttributes } from "react";
import "./form.css";

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "size"> {
  label: string;
  hint?: string;
  error?: string;
  size?: "sm" | "md";
}

export function Select({
  label,
  hint,
  error,
  required,
  size = "md",
  id: idProp,
  children,
  ...rest
}: SelectProps) {
  const autoId = useId();
  const fieldId = idProp ?? autoId;
  return (
    <label className="lw-field" htmlFor={fieldId}>
      <span>
        {label}
        {required ? (
          <span className="lw-required" aria-hidden="true">
            {" "}
            *
          </span>
        ) : null}
      </span>
      <select
        id={fieldId}
        className={`lw-select${size === "sm" ? " lw-select-sm" : ""}`}
        aria-describedby={error ? `${fieldId}-error` : hint ? `${fieldId}-hint` : undefined}
        aria-invalid={error ? true : undefined}
        {...rest}
      >
        {children}
      </select>
      {error ? (
        <span className="lw-error-text" id={`${fieldId}-error`} role="alert">
          {error}
        </span>
      ) : hint ? (
        <span className="lw-hint" id={`${fieldId}-hint`}>
          {hint}
        </span>
      ) : null}
    </label>
  );
}

export default Select;
