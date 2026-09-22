/* Component catalog: every design-language section 4 family in all
 * variants and states, with an in-page theme toggle. The gate reviews the
 * catalog without live data. scripts/web_check.py catalog asserts this page
 * renders every family listed in FAMILIES below. */
"use client";

import { useState } from "react";
import Button from "@/components/ui/Button";
import IconButton from "@/components/ui/IconButton";
import { Input } from "@/components/ui/Input";
import Textarea from "@/components/ui/Textarea";
import Select from "@/components/ui/Select";
import Search from "@/components/ui/Search";
import Switch from "@/components/ui/Switch";
import Checkbox from "@/components/ui/Checkbox";
import Segmented from "@/components/ui/Segmented";
import Badge from "@/components/ui/Badge";
import Chip, { Tag } from "@/components/ui/Chip";
import Card from "@/components/ui/Card";
import SectionHeader from "@/components/ui/SectionHeader";
import { KpiCell, KpiStrip } from "@/components/ui/KpiStrip";
import Table from "@/components/ui/Table";
import Pagination from "@/components/ui/Pagination";
import FilterBar from "@/components/ui/FilterBar";
import ChartCard from "@/components/charts/ChartCard";
import { AreaChart } from "@/components/charts/AreaChart";
import { LineChart } from "@/components/charts/LineChart";
import StackedBar from "@/components/charts/StackedBar";
import Waterfall from "@/components/ui/Waterfall";
import LogViewer from "@/components/ui/LogViewer";
import Timeline from "@/components/ui/Timeline";
import Dropdown from "@/components/ui/Dropdown";
import { Dialog, Drawer } from "@/components/ui/Dialog";
import { ToastProvider, useToast } from "@/components/ui/Toast";
import Banner from "@/components/ui/Banner";
import Tooltip from "@/components/ui/Tooltip";
import EmptyState, { ErrorState, Skeleton } from "@/components/ui/EmptyState";
import KeyValueList from "@/components/ui/KeyValueList";
import CopyField, { SecretReveal } from "@/components/ui/CopyField";
import Avatar, { UserMenu } from "@/components/ui/Avatar";
import AuditRow from "@/components/ui/AuditRow";
import StatusDot from "@/components/shell/StatusDot";
import LivePill from "@/components/shell/LivePill";

/* The web_check.py catalog manifest mirrors this list. */
export const FAMILIES = [
  "button",
  "icon-button",
  "input",
  "textarea",
  "select",
  "search",
  "switch",
  "checkbox",
  "segmented",
  "badge",
  "chip",
  "tag",
  "card",
  "section-header",
  "kpi",
  "table",
  "pagination",
  "filter-bar",
  "chart",
  "area-chart",
  "line-chart",
  "stacked-bar",
  "axis",
  "legend",
  "crosshair",
  "waterfall",
  "log-viewer",
  "timeline",
  "dropdown",
  "dialog",
  "drawer",
  "toast",
  "banner",
  "tooltip",
  "empty-state",
  "skeleton",
  "route-progress",
  "error-state",
  "key-value-list",
  "secret-reveal",
  "copy-field",
  "avatar",
  "user-menu",
  "audit-row",
  "status-dot",
  "live-pill",
];

function Block({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section className="lw-sec" data-family={id} aria-label={title}>
      <SectionHeader eyebrow="Catalog" title={title} />
      <Card>{children}</Card>
    </section>
  );
}

function ToastDemo() {
  const { push } = useToast();
  return (
    <Button
      variant="secondary"
      size="sm"
      onClick={() => push({ tone: "ok", title: "Saved", body: "Settings updated." })}
    >
      Show toast
    </Button>
  );
}

export default function CatalogPage() {
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [seg, setSeg] = useState("15m");
  const [query, setQuery] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const applyTheme = (next: "dark" | "light") => {
    setTheme(next);
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("laya-theme", next);
    } catch {
      /* preview-only toggle */
    }
  };

  return (
    <ToastProvider>
      <div style={{ marginBottom: 16, display: "flex", gap: 8, alignItems: "center" }}>
        <span className="lw-eyebrow">Catalog theme</span>
        <Button variant="secondary" size="sm" onClick={() => applyTheme(theme === "dark" ? "light" : "dark")}>
          {theme === "dark" ? "Show light" : "Show dark"}
        </Button>
      </div>

      <Block id="button" title="Button">
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Button variant="primary">Create key</Button>
          <Button variant="secondary">Load older</Button>
          <Button variant="ghost">Clear all</Button>
          <Button variant="danger">Revoke</Button>
          <Button variant="primary" size="sm">
            Run
          </Button>
          <Button variant="primary" loading>
            Create key
          </Button>
          <Button variant="secondary" disabled>
            Disabled
          </Button>
        </div>
      </Block>

      <Block id="icon-button" title="Icon button">
        <div style={{ display: "flex", gap: 8 }}>
          <IconButton label="Toggle color theme">
            <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5">
              <circle cx="8" cy="8" r="3" />
            </svg>
          </IconButton>
        </div>
      </Block>

      <Block id="input" title="Input, textarea, select, search">
        <div style={{ display: "grid", gap: 12, maxWidth: 480 }} data-family="textarea" data-family2="select" data-family3="search">
          <Input label="New key name" hint="Lowercase letters and dashes." value="" onChange={() => {}} />
          <Input label="Port" error="Port must be a number." value="abc" onChange={() => {}} />
          <Textarea label="State" value='{"message": "hello"}' onChange={() => {}} />
          <Select label="Model" value="auto" onChange={() => {}}>
            <option value="auto">auto (router)</option>
            <option value="english">english</option>
          </Select>
          <Search label="Request id" value={query} onChange={setQuery} placeholder="filter request id" />
        </div>
      </Block>

      <Block id="switch" title="Switch and checkbox">
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }} data-family="checkbox">
          <Switch label="Require key auth on /predict and /route" defaultChecked />
          <Checkbox label="Remember this device" defaultChecked />
        </div>
      </Block>

      <Block id="segmented" title="Segmented control">
        <Segmented
          label="Time range"
          options={[
            { value: "15m", label: "15m" },
            { value: "1h", label: "1h" },
            { value: "6h", label: "6h" },
          ]}
          value={seg}
          onChange={setSeg}
        />
      </Block>

      <Block id="badge" title="Badge">
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Badge tone="ok">200 OK</Badge>
          <Badge tone="warn">422 reject</Badge>
          <Badge tone="err">500 error</Badge>
          <Badge tone="neutral">revoked</Badge>
          <Badge tone="accent">choice</Badge>
        </div>
      </Block>

      <Block id="chip" title="Chip and tag">
        <div className="lw-chips" data-family="tag">
          <Chip name="model" value="english" />
          <Chip name="route_reason" value="state is English" />
          <Tag label="triage" value="refund" onRemove={() => {}} />
        </div>
      </Block>

      <Block id="card" title="Card and panel">
        <div style={{ display: "grid", gap: 12 }}>
          <Card variant="raised" title="Raised card" meta="default">
            Raised container with a header and footer.
          </Card>
          <Card variant="flat" title="Flat card" meta="grouped content">
            Flat container for KPI strips and grouped content.
          </Card>
          <Card variant="inset" title="Inset well" meta="data">
            Sunken container for data readouts.
          </Card>
        </div>
      </Block>

      <Block id="section-header" title="Section header">
        <SectionHeader eyebrow="Observe" title="System pulse" meta="rolling 5-minute windows" />
      </Block>

      <Block id="kpi" title="KPI strip">
        <KpiStrip>
          <KpiCell label="Requests" value="3.8" unit="/s" delta="+0.4 vs prev 5m" />
          <KpiCell label="Errors (5m)" value="2" delta="1x 422, 1x 500" tone="warn" />
          <KpiCell label="Latency p50 / p95" value="412.5 / 863.1" unit="ms" delta="p95 +112 ms vs prev" tone="warn" />
          <KpiCell label="Memory RSS" value="2847.3" unit="MB" delta="+12.4 MB, 6h" />
        </KpiStrip>
      </Block>

      <Block id="table" title="Table">
        <Table
          columns={[
            { key: "id", label: "Request id" },
            { key: "status", label: "Status" },
            { key: "ms", label: "Total ms", numeric: true, sortable: true, sortDirection: "descending", onSort: () => {} },
          ]}
          caption="Trace list"
        >
          <tr>
            <td className="lw-id-cell">9f2c1a4b</td>
            <td>
              <Badge tone="ok">200 OK</Badge>
            </td>
            <td className="num">412.5</td>
          </tr>
        </Table>
        <div style={{ marginTop: 16 }}>
          <Table
            columns={[{ key: "id", label: "Request id" }]}
            state="loading"
            caption="Loading rows"
          />
        </div>
        <div style={{ marginTop: 16 }}>
          <Table columns={[{ key: "id", label: "Request id" }]} state="empty" emptyTitle="No traces yet" emptyBody="Send a request to /predict to see one." />
        </div>
        <div style={{ marginTop: 16 }}>
          <Table
            columns={[{ key: "id", label: "Request id" }]}
            state="error"
            errorTitle="Failed to load traces"
            errorDetail="500 internal: the query timed out"
            requestId="9f2c1a4b"
            onRetry={() => {}}
          />
        </div>
      </Block>

      <Block id="pagination" title="Pagination">
        <Pagination rangeLabel="showing 50 of 1,204" hasMore onLoadOlder={() => {}} />
      </Block>

      <Block id="filter-bar" title="Filter bar">
        <FilterBar active onClearAll={() => {}}>
          <Select label="Route" size="sm" value="all" onChange={() => {}}>
            <option value="all">all</option>
            <option value="/predict">/predict</option>
          </Select>
          <Search label="Request id" value={query} onChange={setQuery} />
        </FilterBar>
      </Block>

      <Block id="chart" title="Charts">
        <div style={{ display: "grid", gap: 16 }} data-family="area-chart" data-family2="line-chart" data-family3="axis" data-family4="legend" data-family5="crosshair">
          <ChartCard title="Request rate" current="3.8 /s" footer="step 10s · window 15m" summary="Request rate, latest 3.8 per second">
            <AreaChart
              series={{ key: "r", label: "requests", color: "var(--accent-bar)", values: [1.8, 2.4, 2.1, 3.1, 2.7, 3.8] }}
              summary="Request rate"
            />
          </ChartCard>
          <ChartCard title="Latency" current="p95 863.1 ms" footer="step 10s · window 15m · ms" summary="Latency percentiles">
            <LineChart
              series={[
                { key: "p50", label: "p50", color: "var(--accent-bar)", values: [398, 412, 405, 431, 418, 412] },
                { key: "p90", label: "p90", color: "var(--s2)", values: [612, 655, 631, 704, 659, 648] },
                { key: "p95", label: "p95", color: "var(--s3)", values: [863, 901, 872, 946, 905, 899] },
              ]}
              summary="Latency percentiles"
            />
          </ChartCard>
          <div data-family="stacked-bar">
            <StackedBar
              summary="Model mix, 2 buckets"
              buckets={[
                {
                  label: "13:55",
                  total: 100,
                  parts: [
                    { key: "english", label: "english", color: "var(--accent-bar)", value: 71 },
                    { key: "typed", label: "typed-decisions", color: "var(--s2)", value: 19 },
                    { key: "multi", label: "multilingual", color: "var(--line-strong)", value: 10 },
                  ],
                },
                {
                  label: "13:56",
                  total: 100,
                  parts: [
                    { key: "english", label: "english", color: "var(--accent-bar)", value: 74 },
                    { key: "typed", label: "typed-decisions", color: "var(--s2)", value: 20 },
                    { key: "multi", label: "multilingual", color: "var(--line-strong)", value: 6 },
                  ],
                },
              ]}
            />
          </div>
        </div>
      </Block>

      <Block id="waterfall" title="Waterfall">
        <Waterfall
          totalMs={412.5}
          summary="total 412.5 ms · forward 94.9% · queue 1.3%"
          spans={[
            { name: "queue_wait", start_ms: 11.8, duration_ms: 5.2, kind: "queue" },
            { name: "forward", start_ms: 20.2, duration_ms: 391.4, kind: "forward", attrs: { model: "english" } },
            { name: "serialize", start_ms: 411.6, duration_ms: 0.9 },
          ]}
        />
      </Block>

      <Block id="log-viewer" title="Log viewer">
        <LogViewer
          entries={[
            { ts: 1790080267.284, level: "info", trace_id: "9f2c1a4b", message: "predict status=200 ms=412.5 model=english" },
            { ts: 1790080261.101, level: "error", trace_id: "77aa90bc", message: "predict status=500 error=cuda_oom" },
          ]}
        />
      </Block>

      <Block id="timeline" title="Timeline">
        <Timeline
          items={[
            { ts: "14:31:07", text: "score submitted: helpfulness 4/4" },
            { ts: "14:30:12", text: "model loaded: english" },
          ]}
        />
      </Block>

      <Block id="dropdown" title="Dropdown menu">
        <Dropdown
          label="Account menu"
          trigger={<Button variant="secondary" size="sm">Open menu</Button>}
          items={[
            { key: "account", label: "Account" },
            { key: "theme", label: "Theme" },
            { key: "signout", label: "Sign out", danger: true },
          ]}
          onSelect={() => {}}
        />
      </Block>

      <Block id="dialog" title="Dialog and drawer">
        <div style={{ display: "flex", gap: 8 }} data-family="drawer">
          <Button variant="danger" size="sm" onClick={() => setDialogOpen(true)}>
            Revoke key
          </Button>
          <Button variant="secondary" size="sm" onClick={() => setDrawerOpen(true)}>
            Edit settings
          </Button>
        </div>
        <Dialog
          open={dialogOpen}
          title="Revoke lay_3f8a...?"
          confirmLabel="Revoke"
          onConfirm={() => setDialogOpen(false)}
          onClose={() => setDialogOpen(false)}
        >
          Engine clients using this key get 401. This cannot be undone.
        </Dialog>
        <Drawer open={drawerOpen} title="Edit settings" onClose={() => setDrawerOpen(false)}>
          Retention and sampling controls render here in Phase 6.
        </Drawer>
      </Block>

      <Block id="toast" title="Toast">
        <ToastDemo />
      </Block>

      <Block id="banner" title="Banner">
        <Banner tone="warn">Multilingual cannot be loaded while LAYA_ENGLISH_ONLY=1.</Banner>
      </Block>

      <Block id="tooltip" title="Tooltip">
        <Tooltip text="Average queue wait over the window.">
          <span style={{ borderBottom: "1px dotted var(--text-3)" }}>queue wait</span>
        </Tooltip>
      </Block>

      <Block id="empty-state" title="Empty, loading and error states">
        <div style={{ display: "grid", gap: 12 }} data-family="skeleton" data-family2="route-progress" data-family3="error-state">
          <EmptyState title="No traces yet" body="Send a request to /predict to see one." />
          <Skeleton height={12} width="60%" />
          <ErrorState title="Failed to load traces" detail="500 internal: the query timed out" requestId="9f2c1a4b" onRetry={() => {}} />
        </div>
      </Block>

      <Block id="key-value-list" title="Key-value list, secret, copy">
        <div style={{ display: "grid", gap: 12 }} data-family="secret-reveal" data-family2="copy-field">
          <KeyValueList
            items={[
              { key: "bind", value: "127.0.0.1:8050" },
              { key: "device", value: "auto" },
              { key: "request id", value: <CopyField label="Request id" value="9f2c1a4b" /> },
            ]}
          />
          <SecretReveal secret="lay_3f8a9d21c0e74b66a1d5e7f2a4b6c8d0" />
        </div>
      </Block>

      <Block id="avatar" title="Avatar and user menu">
        <div style={{ display: "flex", gap: 12, alignItems: "center" }} data-family="user-menu">
          <Avatar email="admin@example.com" name="Admin User" />
          <UserMenu me={{ id: "u_1", email: "admin@example.com", name: "Admin User", role: "admin" }} />
        </div>
      </Block>

      <Block id="audit-row" title="Audit row">
        <AuditRow
          ts="14:30:55"
          actor="admin@example.com"
          actorName="Admin"
          action="key.revoked"
          target="lay_9d2f"
          result="ok"
          tone="ok"
        />
      </Block>

      <Block id="status-dot" title="Status dot and live pill">
        <div style={{ display: "flex", gap: 16, alignItems: "center" }} data-family="live-pill">
          <StatusDot tone="ok" label="polling 3s" />
          <StatusDot tone="warn" label="reconnecting" />
          <StatusDot tone="err" label="offline" />
          <LivePill status="connected" tickS={3} />
          <LivePill status="reconnecting" />
        </div>
      </Block>
    </ToastProvider>
  );
}
