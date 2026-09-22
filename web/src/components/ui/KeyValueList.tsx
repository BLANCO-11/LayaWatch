/* Key-value list. Contract: design-language section 4.27.
 * Definition list, mono uppercase key 160px, hairline rows, last row
 * borderless, stacks under 640px. Secrets route to SecretReveal, ids to
 * CopyField (composed by the caller). */
import "./overlays.css";

export interface KeyValue {
  key: string;
  value: React.ReactNode;
}

export default function KeyValueList({ items }: { items: KeyValue[] }) {
  return (
    <dl className="lw-dl">
      {items.map((item) => (
        <div key={item.key}>
          <dt>{item.key}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}
