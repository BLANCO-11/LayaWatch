/* Users shell. User management wiring lands in Phase 6. */
import SectionHeader from "@/components/ui/SectionHeader";
import EmptyState from "@/components/ui/EmptyState";
import Card from "@/components/ui/Card";

export default function UsersPage() {
  return (
    <section className="lw-sec" aria-label="Users">
      <SectionHeader eyebrow="Admin" title="Users" meta="owner managed" />
      <Card variant="flat">
        <EmptyState title="Users land in Phase 6" body="Invite an owner or admin to manage access." />
      </Card>
    </section>
  );
}
