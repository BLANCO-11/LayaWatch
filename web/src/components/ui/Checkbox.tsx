/* Checkbox: 16px, radius 4, accent fill when checked. Section 4.4. */
import type { InputHTMLAttributes } from "react";
import "./form.css";

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label: string;
}

export function Checkbox({ label, ...rest }: CheckboxProps) {
  return (
    <label className="lw-check">
      <input type="checkbox" {...rest} />
      {label}
    </label>
  );
}

export default Checkbox;
