/* Checkpoints shell. Model load/unload wiring lands in Phase 6. */
import SectionHeader from "@/components/ui/SectionHeader";
import EmptyState from "@/components/ui/EmptyState";
import Card from "@/components/ui/Card";

export default function CheckpointsPage() {
  return (
    <section className="lw-sec" aria-label="Checkpoints">
      <SectionHeader eyebrow="Operate" title="Checkpoints" meta="model inventory" />
      <Card variant="flat">
        <EmptyState
          title="Checkpoints land in Phase 6"
          body="Loaded and available models render here."
        />
      </Card>
    </section>
  );
}
