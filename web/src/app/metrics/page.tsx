/* Metrics view: server-driven range (15m, 6h, 7d) reflected in the URL, four
 * charts plotted from /api/v1/metrics points verbatim (no client re-bucketing,
 * gaps render as breaks) plus the /api/v1/metrics/models stacked bars.
 * Plan: phase-5 task 9; criterion 6. */
"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import SectionHeader from "@/components/ui/SectionHeader";
import EmptyState, { ErrorState, Skeleton } from "@/components/ui/EmptyState";
import Card from "@/components/ui/Card";
import Segmented from "@/components/ui/Segmented";
import { AreaChart } from "@/components/charts/AreaChart";
import { LineChart } from "@/components/charts/LineChart";
import StackedBar, { type MixBucket } from "@/components/charts/StackedBar";
import { useSyncedFilters } from "@/lib/filters";
import { api, ApiError } from "@/lib/api";
import type { MetricPoint, MetricsResponse, ModelsMixResponse } from "@/lib/api";
import { clockTime } from "@/lib/format";

type LoadState = "loading" | "ready" | "empty" | "error";

const DEFAULTS = { range: "15m" };
const MIX_COLORS = ["var(--accent-bar)", "var(--s2)", "var(--s3)"];

function quantileAt(point: MetricPoint | undefined, key: string): number {
  const value = point?.[1];
  if (value !== null && typeof value === "object") {
    const quantile = value[key];
    return typeof quantile === "number" ? quantile : 0;
  }
  return 0;
}

export default function MetricsPage() {
  return (
    <Suspense fallback={null}>
      <MetricsContent />
    </Suspense>
  );
}

function MetricsContent() {
  const { filters, setFilter } = useSyncedFilters(DEFAULTS);
  const range = filters.range;
  const [state, setState] = useState<LoadState>("loading");
  const [series, setSeries] = useState<MetricsResponse | null>(null);
  const [mix, setMix] = useState<ModelsMixResponse | null>(null);
  const [detail, setDetail] = useState<string | undefined>(undefined);
  const [requestId, setRequestId] = useState<string | undefined>(undefined);

  const load = useCallback(() => {
    setState("loading");
    Promise.all([
      api.get<MetricsResponse>(
        `/api/v1/metrics?metrics=requests,latency,errors,queue&range=${range}`,
      ),
      api.get<ModelsMixResponse>(`/api/v1/metrics/models?range=${range}`),
    ])
      .then(([nextSeries, nextMix]) => {
        setSeries(nextSeries);
        setMix(nextMix);
        setState("ready");
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
  }, [range]);

  useEffect(load, [load]);

  const step = series?.step ?? 10;
  const footer = `step ${step}s · window ${range}`;
  const points = (metric: string): MetricPoint[] =>
    series?.series?.find((entry) => entry.metric === metric)?.points ?? [];

  const requestPoints = points("requests");
  const latencyPoints = points("latency");
  const errorPoints = points("errors");
  const queuePoints = points("queue");
  const times = requestPoints.map(([ts]) => clockTime(ts));

  const rateValues = requestPoints.map((point) =>
    typeof point[1] === "number" ? point[1] : 0,
  );
  /* Error rate per bucket: errors / requests as a percentage of that bucket. */
  const errorRateValues = errorPoints.map((point, index) => {
    const requestCount = rateValues[index] * step;
    if (requestCount <= 0) return 0;
    const errorCount = typeof point[1] === "number" ? point[1] : 0;
    return Math.round((errorCount / requestCount) * 1000) / 10;
  });
  const queueValues = queuePoints.map((point) =>
    typeof point[1] === "number" ? point[1] : 0,
  );
  const p50Values = latencyPoints.map((point) => quantileAt(point, "p50"));
  const p90Values = latencyPoints.map((point) => quantileAt(point, "p90"));
  const p95Values = latencyPoints.map((point) => quantileAt(point, "p95"));

  const rateMax = Math.max(1, ...rateValues);
  const errorMax = Math.max(1, ...errorRateValues);
  const queueMax = Math.max(1, ...queueValues);
  const latMax = Math.max(1, ...p50Values, ...p90Values, ...p95Values);

  const mixBuckets: MixBucket[] = (mix?.buckets ?? []).map((bucket) => ({
    label: clockTime(bucket.ts),
    total: Object.values(bucket.counts).reduce((sum, count) => sum + count, 0),
    parts: (mix?.models ?? []).map((model, index) => ({
      key: model,
      label: model,
      color: MIX_COLORS[index % MIX_COLORS.length],
      value: bucket.counts[model] ?? 0,
    })),
  }));
  const noData = rateValues.length === 0 || rateValues.every((value) => value === 0);

  const chartGrid = {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
    gap: 16,
  } as const;

  return (
    <section className="lw-sec" aria-label="Time series">
      <SectionHeader
        eyebrow="Observe"
        title="Time series"
        meta={
          series
            ? `${series.note ?? "percentiles are bucket-level"} · thresholds: p95 > 1 s, error rate > 2 %`
            : "server picks the step"
        }
      />
      <div style={{ marginBottom: 16 }}>
        <Segmented
          label="range"
          options={[
            { value: "15m", label: "15m" },
            { value: "6h", label: "6h" },
            { value: "7d", label: "7d" },
          ]}
          value={range}
          onChange={(value) => setFilter("range", value)}
        />
      </div>

      {state === "loading" ? (
        <div style={chartGrid} aria-busy="true" aria-label="Loading charts">
          {["Request rate", "Latency", "Error rate", "Queue wait"].map((title) => (
            <Card key={title} title={title}>
              <Skeleton height={170} />
            </Card>
          ))}
        </div>
      ) : state === "error" ? (
        <ErrorState
          title="Failed to load metrics"
          detail={detail}
          requestId={requestId}
          onRetry={load}
        />
      ) : state === "empty" || noData ? (
        <Card variant="flat">
          <EmptyState
            title="No data in this window"
            body="Send a request to /predict to start a series."
          />
        </Card>
      ) : (
        <div style={chartGrid}>
          <Card
            title="Request rate"
            meta={`${rateValues[rateValues.length - 1]?.toFixed(1) ?? "0"} /s`}
            footer={`${footer} · req/s`}
          >
            <AreaChart
              series={{ key: "requests", label: "requests", color: "var(--accent-bar)", values: rateValues }}
              max={rateMax}
              unit="/s"
              times={times}
              summary={`Request rate, window ${range}, step ${step} seconds`}
            />
          </Card>
          <Card
            title="Latency"
            meta={`p95 ${p95Values[p95Values.length - 1]?.toFixed(1) ?? "0"} ms`}
            footer={`${footer} · ms`}
          >
            <LineChart
              series={[
                { key: "p50", label: "p50", color: "var(--accent-bar)", values: p50Values },
                { key: "p90", label: "p90", color: "var(--s2)", values: p90Values },
                { key: "p95", label: "p95", color: "var(--s3)", values: p95Values },
              ]}
              max={latMax}
              unit="ms"
              times={times}
              summary={`Latency percentiles, window ${range}, step ${step} seconds`}
            />
          </Card>
          <Card
            title="Error rate"
            meta={`${errorRateValues[errorRateValues.length - 1]?.toFixed(1) ?? "0"} %`}
            footer={`${footer} · percent of requests`}
          >
            <LineChart
              series={[
                { key: "errors", label: "error rate", color: "var(--err)", values: errorRateValues },
              ]}
              max={errorMax}
              unit="%"
              times={times}
              summary={`Error rate per bucket, window ${range}, step ${step} seconds`}
            />
          </Card>
          <Card
            title="Queue wait"
            meta={`${queueValues[queueValues.length - 1]?.toFixed(1) ?? "0"} ms avg`}
            footer={`${footer} · ms`}
          >
            <LineChart
              series={[
                { key: "queue", label: "queue wait", color: "var(--warn)", values: queueValues },
              ]}
              max={queueMax}
              unit="ms"
              times={times}
              summary={`Queue wait per bucket, window ${range}, step ${step} seconds`}
            />
          </Card>
        </div>
      )}

      {state === "ready" && !noData ? (
        <div style={{ marginTop: 24 }}>
          <SectionHeader
            eyebrow="Observe"
            title="Model mix"
            meta={`decisions per ${step}s bucket · ${mix?.total.toLocaleString("en-US") ?? "0"} total`}
          />
          <Card variant="flat">
            <StackedBar
              buckets={mixBuckets}
              summary={`Model mix per ${step} second bucket, window ${range}, ${mix?.total ?? 0} decisions total`}
            />
          </Card>
        </div>
      ) : null}
    </section>
  );
}
