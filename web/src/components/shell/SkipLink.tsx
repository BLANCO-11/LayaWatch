/* Skip link: first focusable element. Contract: design-language section 7. */
import "./shell.css";

export default function SkipLink() {
  return (
    <a className="lw-skip" href="#lw-content">
      Skip to content
    </a>
  );
}
