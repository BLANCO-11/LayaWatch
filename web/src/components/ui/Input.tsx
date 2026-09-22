/* Input field. Contract: design-language section 4.3.
 * Always-visible mono label, hint XOR error with aria-describedby and
 * aria-invalid, validation on blur and submit. */
"use client";

import { useId, useState, type InputHTMLAttributes } from "react";
import "./form.css";

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "size"> {
  label: string;
  hint?: string;
  error?: string;
  validate?: (value: string) => string | undefined;
  size?: "sm" | "md";
}

export function Input({
  label,
  hint,
  error,
  required,
  size = "md",
  id: idProp,
  validate,
  value,
  onBlur,
  ...rest
}: InputProps) {
  const autoId = useId();
  const fieldId = idProp ?? autoId;
  const [touched, setTouched] = useState(false);
  const liveError = touched ? (error ?? validate?.(String(value ?? ""))) : undefined;
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
      <input
        id={fieldId}
        className={`lw-input${size === "sm" ? " lw-input-sm" : ""}`}
        value={value}
        aria-describedby={liveError ? `${fieldId}-error` : hint ? `${fieldId}-hint` : undefined}
        aria-invalid={liveError ? true : undefined}
        onBlur={(e) => {
          setTouched(true);
          onBlur?.(e);
        }}
        {...rest}
      />
      {liveError ? (
        <span className="lw-error-text" id={`${fieldId}-error`} role="alert">
          {liveError}
        </span>
      ) : hint ? (
        <span className="lw-hint" id={`${fieldId}-hint`}>
          {hint}
        </span>
      ) : null}
    </label>
  );
}

export default Input;
