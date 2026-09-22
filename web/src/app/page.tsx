/* Overview: KPI strip from /api/v1/metrics/summary (with previous-window deltas
 * and the conditional Throttled cell), system pulse from /api/v1/meta, rate and
 * latency charts from /api/v1/metrics, recent traces from /api/v1/traces
 * prepended by SSE `trace` events. Plan: phase-5 tasks 3 and 4. */
"use client";

import { useEffect, useState } from "react";
import SectionHeader from "@/components/ui/SectionHeader";
import EmptyState, { ErrorState, Skeleton } from "@/components/ui/EmptyState";
import Card from "@/components/ui/Card";
import { KpiCell, KpiStrip } from "@/components/ui/KpiStrip";
import { AreaChart } from "@/components/charts/AreaChart";
import { LineChart } from "@/components/charts/LineChart";
import TraceTable from "@/components/ui/TraceTable";
import { useStream } from "@/lib/stream";
import { api, ApiError } from "@/lib/api";
import type {
  HealthResponse,
  MetaResponse,
  MetricPoint,
  MetricsResponse,
  MetricsSummary,
  Paged,
  TraceSummary,
} from "@/lib/api";
import { clockTime, durationShort, thousands } from "@/lib/format";

type LoadState = "loading" | "ready" | "empty" | "error";

const CHART_RANGE = "15m";

function percentile(point: MetricPoint | undefined, key: string): number {
  const value = point?.[1];
  if (value !== null && typeof value === "object") {
    const v = value[key];
    return typeof v === "number" ? v : 0;
  }
  return 0;
}

export default function OverviewPage() {
  const [state, setState] = useState<LoadState>("loading");
  const [summary, setSummary] = useState<MetricsSummary | null>(null);
  const [meta, setMeta] = useState<MetaResponse | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);
  const [traces, setTraces] = useState<Paged<TraceSummary> | null>(null);
  const [detail, setDetail] = useState<string | undefined>(undefined);
  const [requestId, setRequestId] = useState<string | undefined>(undefined);
  const [newTraces, setNewTraces] = useState(0);

  const load = () => {
    setState("loading");
    Promise.all([
      api.get<MetricsSummary>("/api/v1/metrics/summary"),
      api.get<MetaResponse>("/api/v1/meta"),
      api.get<HealthResponse>("/healthz"),
      api.get<MetricsResponse>(`/api/v1/metrics?metrics=requests,latency&range=${CHART_RANGE}`),
      api.get<Paged<TraceSummary>>("/api/v1/traces?limit=10"),
    ])
      .then(([nextSummary, nextMeta, nextHealth, nextMetrics, nextTraces]) => {
        setSummary(nextSummary);
        setMeta(nextMeta);
        setHealth(nextHealth);
        setMetrics(nextMetrics);
        setTraces(nextTraces);
        setState(nextSummary.total_requests === 0 ? "empty" : "ready");
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError) {
          setDetail(`${err.status} ${err.code}: ${err.message}`);
          setRequestId(err.requestId);
        } else {
          setDetail("The server could not be reached. Check that the process is running.");
          setRequestId(undefined);
        }
        setState("error");
      });
  };

  /* Silent fact refresh on every stream tick (KPI values, throttled cell, pulse). */
  const refreshFacts = () => {
    void Promise.all([
      api.get<MetricsSummary>("/api/v1/metrics/summary"),
      api.get<MetaResponse>("/api/v1/meta"),
      api.get<HealthResponse>("/healthz"),
    ])
      .then(([nextSummary, nextMeta, nextHealth]) => {
        setSummary(nextSummary);
        setMeta(nextMeta);
        setHealth(nextHealth);
        setState(nextSummary.total_requests === 0 ? "empty" : "ready");
      })
      .catch(() => {});
  };

  useEffect(load, []);

  const prependTrace = (trace: TraceSummary) => {
    setTraces((prev) => {
      if (!prev || prev.items.some((item) => item.id === trace.id)) return prev;
      return {
        ...prev,
        items: [trace, ...prev.items].slice(0, 50),
        total_estimate: prev.total_estimate + 1,
      };
    });
    setNewTraces((count) => count + 1);
  };

  useStream({
    onPulse: refreshFacts,
    onTrace: prependTrace,
    onPoll: (data) => {
      if (data.summary) setSummary(data.summary);
      if (data.traces) setTraces(data.traces);
    },
  });

  const windowMin = summary ? Math.round(summary.range_s / 60) : 15;
  const ring = typeof meta?.config?.ring_traces === "number" ? meta.config.ring_traces : null;
  const loaded = health?.models ?? [];
  const configured = meta?.config?.models ?? [];
  const notLoaded = configured.filter((name) => !loaded.includes(name));
  const ratePoints = metrics?.series?.find((s) => s.metric === "requests")?.points ?? [];
  const latencyPoints = metrics?.series?.find((s) => s.metric === "latency")?.points ?? [];
  const chartTimes = ratePoints.map(([ts]) => clockTime(ts));
  const rateValues = ratePoints.map((point) =>
    typeof point[1] === "number" ? point[1] : 0,
  );
  const rateMax = Math.max(1, ...rateValues);
  const p50Values = latencyPoints.map((point) => percentile(point, "p50"));
  const p90Values = latencyPoints.map((point) => percentile(point, "p90"));
  const p95Values = latencyPoints.map((point) => percentile(point, "p95"));
  const latMax = Math.max(1, ...p50Values, ...p90Values, ...p95Values);
  const step = metrics?.step ?? 10;
  const errorsByStatus = summary
    ? Object.entries(summary.errors_by_status)
        .map(([status, count]) => `${count}× ${status}`)
        .join(" · ")
    : "";

  return (
    <div>
      <div className="lw-sr-only" role="status" aria-live="polite">
        {newTraces > 0 ? `${newTraces} new trace${newTraces === 1 ? "" : "s"}` : ""}
      </div>

      <section className="lw-sec" aria-label="System pulse">
        <SectionHeader
          eyebrow="Observe"
          title="System pulse"
          meta={`rolling ${windowMin}m windows · refresh 3s`}
        />
        {state === "loading" ? (
          <div className="lw-kpis" aria-busy="true" aria-label="Loading summary">
            {Array.from({ length: 4 }, (_, i) => (
              <div className="lw-kpi" key={i} aria-hidden="true">
                <Skeleton height={10} width="50%" />
                <div style={{ marginTop: 8 }}>
                  <Skeleton height={24} width="70%" />
                </div>
              </div>
            ))}
          </div>
        ) : state === "error" ? (
          <ErrorState
            title="Failed to load metrics"
            detail={detail}
            requestId={requestId}
            onRetry={load}
          />
        ) : state === "empty" || !summary || !meta || !health ? (
          <Card variant="flat">
            <EmptyState
              title="No requests yet"
              body="Send a request to /predict to see the system pulse."
            />
          </Card>
        ) : (
          <KpiStrip>
            <KpiCell
              label="Requests"
              value={summary.requests_per_s.toFixed(1)}
              unit="/s"
              delta={`${summary.deltas.requests_per_s >= 0 ? "+" : ""}${summary.deltas.requests_per_s.toFixed(1)} vs prev ${windowMin}m`}
            />
            <KpiCell
              label={`Errors (${windowMin}m)`}
              value={String(summary.errors)}
              delta={errorsByStatus || "no errors in this window"}
              tone={summary.errors > 0 ? "warn" : "plain"}
            />
            <KpiCell
              label="Latency p50 / p95"
              value={`${summary.p50.toFixed(1)} / ${summary.p95.toFixed(1)}`}
              unit="ms"
              delta={`p95 ${summary.deltas.p95 >= 0 ? "+" : ""}${summary.deltas.p95.toFixed(1)} ms vs prev ${windowMin}m`}
              tone={summary.deltas.p95 > 0 ? "warn" : "ok"}
            />
            <KpiCell
              label="Memory RSS"
              value={(meta.rss_mb ?? 0).toFixed(1)}
              unit="MB"
            />
            <KpiCell
              label="Predict queue"
              value={String(meta.write_queue_depth ?? 0)}
              unit="depth"
              delta={`avg wait ${summary.queue_ms.toFixed(1)} ms`}
            />
            <KpiCell
              label="Uptime"
              value={durationShort(meta.uptime_s)}
              delta={`started ${clockTime(meta.started_at)}`}
            />
            <KpiCell
              label="Total requests"
              value={thousands(summary.total_requests)}
              delta={ring !== null ? `${thousands(ring)} in ring` : undefined}
            />
            <KpiCell
              label="Models loaded"
              value={loaded.join(", ") || "none"}
              delta={
                notLoaded.length > 0 ? `${notLoaded.join(", ")} unloaded` : undefined
              }
            />
            {typeof summary.throttled_5m === "number" ? (
              <KpiCell
                label="Throttled (5m)"
                value={String(summary.throttled_5m)}
                delta="429 in the last 5m"
                tone="warn"
              />
            ) : null}
          </KpiStrip>
        )}
      </section>

      <section className="lw-sec" aria-label="Observability counters">
        <SectionHeader
          eyebrow="Observe"
          title="Observability counters"
          meta="from /api/v1/meta"
        />
        {state === "loading" ? (
          <div className="lw-kpis" aria-busy="true" aria-label="Loading counters">
            {Array.from({ length: 5 }, (_, i) => (
              <div className="lw-kpi" key={i} aria-hidden="true">
                <Skeleton height={10} width="50%" />
                <div style={{ marginTop: 8 }}>
                  <Skeleton height={24} width="60%" />
                </div>
              </div>
            ))}
          </div>
        ) : state === "error" || !meta || !traces ? null : (
          <KpiStrip>
            <KpiCell label="Obs dropped" value={thousands(meta.obs_dropped_total ?? 0)} />
            <KpiCell
              label="Write latency"
              value={(meta.write_latency_ms ?? 0).toFixed(1)}
              unit="ms"
            />
            <KpiCell label="DB size" value={(meta.db_size_bytes ?? 0) / 1048576 >= 1
              ? ((meta.db_size_bytes ?? 0) / 1048576).toFixed(1)
              : "0.0"}
              unit="MB"
            />
            <KpiCell
              label="Ring usage"
              value={thousands(traces.total_estimate)}
              unit={ring !== null ? `of ${thousands(ring)}` : "traces"}
            />
            <KpiCell label="SSE clients" value={String(meta.sse_clients ?? 0)} />
          </KpiStrip>
        )}
      </section>

      <section className="lw-sec" aria-label="Rate and latency">
        <SectionHeader
          eyebrow="Observe"
          title="Rate and latency"
          meta={metrics ? `step ${step}s · window ${CHART_RANGE}` : undefined}
        />
        {state === "loading" ? (
          <div
            style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 16 }}
            aria-busy="true"
            aria-label="Loading charts"
          >
            {[0, 1].map((i) => (
              <Card key={i} title={i === 0 ? "Request rate" : "Latency percentiles"}>
                <Skeleton height={170} />
              </Card>
            ))}
          </div>
        ) : state === "error" ? null : state === "empty" ? (
          <Card variant="flat">
            <EmptyState title="No data in this window" body="Send a request to /predict to start a series." />
          </Card>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 16 }}>
            <Card
              title="Request rate"
              meta={`${summary?.requests_per_s.toFixed(1) ?? "0"} /s`}
              footer={`step ${step}s · window ${CHART_RANGE} · req/s`}
            >
              <AreaChart
                series={{
                  key: "requests",
                  label: "requests",
                  color: "var(--accent-bar)",
                  values: rateValues,
                }}
                max={rateMax}
                unit="/s"
                times={chartTimes}
                summary={`Request rate over the last ${CHART_RANGE}, step ${step} seconds`}
              />
            </Card>
            <Card
              title="Latency percentiles"
              meta={`p95 ${summary?.p95.toFixed(1) ?? "0"} ms`}
              footer={`step ${step}s · window ${CHART_RANGE} · ms`}
            >
              <LineChart
                series={[
                  { key: "p50", label: "p50", color: "var(--accent-bar)", values: p50Values },
                  { key: "p90", label: "p90", color: "var(--s2)", values: p90Values },
                  { key: "p95", label: "p95", color: "var(--s3)", values: p95Values },
                ]}
                max={latMax}
                unit="ms"
                times={chartTimes}
                summary={`Latency percentiles over the last ${CHART_RANGE}, step ${step} seconds`}
              />
            </Card>
          </div>
        )}
      </section>

      <section className="lw-sec" aria-label="Recent traces">
        <SectionHeader
          eyebrow="Observe"
          title="Recent traces"
          meta={
            traces && ring !== null
              ? `${thousands(traces.total_estimate)} in ring of ${thousands(ring)} · newest first`
              : "newest first"
          }
        />
        <Card variant="flat">
          <TraceTable
            items={traces?.items ?? []}
            state={
              state === "loading"
                ? "loading"
                : state === "error"
                  ? "error"
                  : state === "empty" || !traces || traces.items.length === 0
                    ? "empty"
                    : "ready"
            }
            errorDetail={detail}
            requestId={requestId}
            onRetry={load}
            caption="Recent traces, newest first"
          />
        </Card>
      </section>
    </div>
  );
}
