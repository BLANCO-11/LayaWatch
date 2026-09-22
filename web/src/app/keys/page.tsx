/* API Keys shell. Key management wiring lands in Phase 6. */
import SectionHeader from "@/components/ui/SectionHeader";
import EmptyState from "@/components/ui/EmptyState";
import Card from "@/components/ui/Card";

export default function KeysPage() {
  return (
    <section className="lw-sec" aria-label="API keys">
      <SectionHeader eyebrow="Admin" title="API Keys" meta="key inventory" />
      <Card variant="flat">
        <EmptyState title="API keys land in Phase 6" body="Create a key to call /predict from an engine client." />
      </Card>
    </section>
  );
}
