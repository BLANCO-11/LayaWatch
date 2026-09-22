/* Textarea: mono 12.5px, vertical resize. Contract: section 4.3. */
"use client";

import { useId, type TextareaHTMLAttributes } from "react";
import "./form.css";

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label: string;
  hint?: string;
  error?: string;
}

export function Textarea({ label, hint, error, required, id: idProp, ...rest }: TextareaProps) {
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
      <textarea
        id={fieldId}
        className="lw-textarea"
        aria-describedby={error ? `${fieldId}-error` : hint ? `${fieldId}-hint` : undefined}
        aria-invalid={error ? true : undefined}
        {...rest}
      />
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

export default Textarea;
