/* Switch. Contract: design-language section 4.4.
 * Styled native checkbox, clickable label, focus-visible ring,
 * knob uses transform transition only. */
import type { InputHTMLAttributes } from "react";
import "./form.css";

export interface SwitchProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label: string;
}

export function Switch({ label, ...rest }: SwitchProps) {
  return (
    <label className="lw-switch">
      <input type="checkbox" {...rest} />
      <span className="lw-track" aria-hidden="true">
        <span className="lw-knob" />
      </span>
      {label}
    </label>
  );
}

export default Switch;
