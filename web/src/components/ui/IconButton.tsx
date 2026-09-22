/* Icon button: 16px icon, 28/34px square, aria-label plus tooltip required. */
import type { ButtonHTMLAttributes } from "react";
import "./button.css";

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  size?: "sm" | "md";
}

export default function IconButton({ label, size = "md", children, ...rest }: IconButtonProps) {
  return (
    <button
      type="button"
      className="lw-icon-btn"
      style={size === "sm" ? { width: 28, height: 28 } : undefined}
      aria-label={label}
      title={label}
      {...rest}
    >
      {children}
    </button>
  );
}
