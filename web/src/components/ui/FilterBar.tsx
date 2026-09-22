/* Filter bar: labelled sunken controls above a table or chart.
 * Contract: design-language section 4.13. Clear all appears only when
 * at least one filter is set. */
import Button from "./Button";
import "./table.css";

export default function FilterBar({
  children,
  active,
  onClearAll,
}: {
  children: React.ReactNode;
  active: boolean;
  onClearAll: () => void;
}) {
  return (
    <div className="lw-filters">
      {children}
      {active ? (
        <Button variant="ghost" size="sm" onClick={onClearAll}>
          Clear all
        </Button>
      ) : null}
    </div>
  );
}
