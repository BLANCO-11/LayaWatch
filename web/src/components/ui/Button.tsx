/* Button and icon button. Contract: design-language sections 4.1-4.2. */
import type { ButtonHTMLAttributes } from "react";
import "./button.css";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  /** React 19 ref-as-prop; forwarded to the native button via rest spread. */
  ref?: React.Ref<HTMLButtonElement>;
}

export default function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  disabled,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      className={`lw-btn lw-btn-${variant} lw-btn-${size}`}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? (
        <span className="lw-btn-loading-dots" aria-hidden="true">
          ...
        </span>
      ) : null}
      {loading ? "Working" : children}
    </button>
  );
}
