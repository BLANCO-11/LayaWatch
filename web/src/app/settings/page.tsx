/* Settings shell. Settings wiring plus the system|dark|light select land in Phase 6. */
import SectionHeader from "@/components/ui/SectionHeader";
import EmptyState from "@/components/ui/EmptyState";
import Card from "@/components/ui/Card";

export default function SettingsPage() {
  return (
    <section className="lw-sec" aria-label="Settings">
      <SectionHeader eyebrow="Admin" title="Settings" meta="server facts · preferences" />
      <Card variant="flat">
        <EmptyState title="Settings land in Phase 6" body="Retention, sampling and theme preferences render here." />
      </Card>
    </section>
  );
}
