/* Trace detail: header chips (model, route_reason, state size, questions,
 * client key), payload-capture banner, server summary line through the
 * Waterfall, scores and lifecycle side rail, and related log lines linking to
 * the pre-filtered Logs view. Plan: phase-5 tasks 6 to 8; criterion 5. */
"use client";

import { useEffect, useState } from "react";
import SectionHeader from "@/components/ui/SectionHeader";
import { ErrorState, Skeleton } from "@/components/ui/EmptyState";
import Card from "@/components/ui/Card";
import Chip from "@/components/ui/Chip";
import Badge from "@/components/ui/Badge";
import Banner from "@/components/ui/Banner";
import CopyField from "@/components/ui/CopyField";
import Timeline, { type TimelineItem } from "@/components/ui/Timeline";
import Waterfall, { type Span } from "@/components/ui/Waterfall";
import { statusLabel, statusTone } from "@/components/ui/TraceTable";
import { logLevelClass } from "@/components/ui/LogViewer";
import { api, ApiError } from "@/lib/api";
import type { Observation, TraceDetail } from "@/lib/api";
import { clockTime, isoWithTz } from "@/lib/format";

type LoadState = "loading" | "ready" | "error";

const TRACES_QUERY_KEY = "lw:traces-query";

function spanKind(observation: Observation): Span["kind"] {
  if (observation.name === "queue.wait") return "queue";
  if (observation.name === "forward") return "forward";
  if (observation.status === "error") return "error";
  return "standard";
}

/* A captured payload is JSON unless truncation cut it; show it
 * pretty-printed when it parses, raw (and labelled) when it does not. */
function prettyPayload(value: string | null | undefined): string {
  if (!value) return "(not captured)";
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return `${value}\n\n(truncated at LAYWATCH_PAYLOAD_MAX)`;
  }
}

function formatBytes(value: number | null | undefined): string {
  if (typeof value !== "number" || value < 0) return "unknown";
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${value} B`;
}

export default function TraceDetailClient() {
  const [id, setId] = useState<string | null>(null);
  const [backHref, setBackHref] = useState("/traces");
  const [state, setState] = useState<LoadState>("loading");
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [errorDetail, setErrorDetail] = useState<string | undefined>(undefined);
  const [requestId, setRequestId] = useState<string | undefined>(undefined);

  useEffect(() => {
    const segments = window.location.pathname.split("/").filter(Boolean);
    setId(segments[1] ?? null);
    try {
      const saved = sessionStorage.getItem(TRACES_QUERY_KEY);
      setBackHref(saved ? `/traces${saved}` : "/traces");
    } catch {
      /* storage unavailable: the back link falls back to the bare list */
    }
  }, []);

  const load = () => {
    if (!id) return;
    setState("loading");
    api
      .get<TraceDetail>(`/api/v1/traces/${encodeURIComponent(id)}`)
      .then((payload) => {
        setDetail(payload);
        setState("ready");
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError) {
          setErrorDetail(`${err.status} ${err.code}: ${err.message}`);
          setRequestId(err.requestId);
        } else {
          setErrorDetail("The server could not be reached. Check that the process is running.");
          setRequestId(undefined);
        }
        setState("error");
      });
  };

  useEffect(load, [id]);

  const trace = detail?.trace;
  const summary = detail?.summary;

  const spans: Span[] = (detail?.observations ?? [])
    .filter((obs) => obs.type === "span" || obs.type === "generation")
    .map((obs) => ({
      name: obs.name,
      start_ms: obs.start_ms,
      duration_ms: obs.duration_ms,
      kind: spanKind(obs),
      attrs: obs.meta ?? undefined,
    }));

  const payloadObs = (detail?.observations ?? []).find((obs) => obs.name === "payload");

  const timeline: TimelineItem[] = detail
    ? [
        ...detail.scores.map((score) => ({
          ts: clockTime(score.ts),
          text: (
            <span>
              score <b>{score.name}</b> = {String(score.value)} ({score.source})
            </span>
          ),
        })),
        ...(detail.observations ?? [])
          .filter(
            (obs) => obs.name !== "payload" && (obs.name === "model.load" || obs.type === "event"),
          )
          .map((obs) => ({
            ts: `+${obs.start_ms.toFixed(1)} ms`,
            text:
              obs.name === "event" || obs.name === "error" ? (
                <span>
                  error <b>{String(obs.meta?.code ?? "")}</b> {String(obs.meta?.message ?? "")}
                </span>
              ) : (
                <span>
                  model loaded <b>{String(obs.meta?.model ?? obs.model ?? "")}</b>
                </span>
              ),
          })),
      ]
    : [];

  return (
    <div>
      <p style={{ marginBottom: 12 }}>
        <a className="lw-btn lw-btn-ghost lw-btn-sm" href={backHref}>
          Back to traces
        </a>
      </p>
      <section className="lw-sec" aria-label={id ? `Trace ${id}` : "Trace"}>
        <SectionHeader
          eyebrow="Observe"
          title={id ? `Trace ${id}` : "Trace"}
          meta={
            trace && summary
              ? `${statusLabel(trace.status)} · total ${trace.duration_ms.toFixed(1)} ms`
              : "loading"
          }
        />
        {state === "loading" || !id ? (
          <Card variant="flat">
            <span className="lw-sr-only" role="status">
              Loading trace
            </span>
            <Skeleton height={16} width="40%" />
            <div style={{ marginTop: 12 }}>
              <Skeleton height={14} width="70%" />
            </div>
            <div style={{ marginTop: 12 }}>
              <Skeleton height={140} />
            </div>
          </Card>
        ) : state === "error" ? (
          <ErrorState
            title="Failed to load trace"
            detail={errorDetail}
            requestId={requestId}
            onRetry={load}
          />
        ) : (
          detail &&
          trace &&
          summary && (
            <Card
              title={`Trace ${trace.id} · ${trace.method.toLowerCase()} ${trace.route}`}
              meta={`total ${trace.duration_ms.toFixed(1)} ms`}
            >
              <div className="lw-chips" style={{ marginBottom: 12 }}>
                <Chip name="model" value={trace.model || "none"} />
                <Chip name="route_reason" value={trace.route_reason || "unknown"} />
                <Chip name="state" value={formatBytes(trace.state_bytes)} />
                <Chip name="questions" value={String(trace.question_count ?? "unknown")} />
                <Chip name="client key" value={trace.client_key_id || "none"} />
                <Badge tone={statusTone(trace.status)}>{statusLabel(trace.status)}</Badge>
              </div>
              <div style={{ marginBottom: 12 }}>
                <CopyField label="Request id" value={trace.id} />
              </div>
              {trace.meta?.payload_capture ? (
                <div style={{ marginBottom: 12 }}>
                  <Banner tone="warn" storageKey="lw-payload-capture">
                    Payload capture is on: request and response bodies were stored with this
                    trace&apos;s spans.
                  </Banner>
                </div>
              ) : null}
              {payloadObs && (payloadObs.input || payloadObs.output) ? (
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
                    gap: 16,
                    marginBottom: 16,
                  }}
                >
                  <Card variant="flat" title="Request" meta="state + questions · PII scrubbed">
                    <pre
                      style={{
                        margin: 0,
                        maxHeight: 480,
                        overflow: "auto",
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                        fontSize: 12,
                      }}
                    >
                      {prettyPayload(payloadObs.input)}
                    </pre>
                  </Card>
                  <Card variant="flat" title="Response" meta="answers · PII scrubbed">
                    <pre
                      style={{
                        margin: 0,
                        maxHeight: 480,
                        overflow: "auto",
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                        fontSize: 12,
                      }}
                    >
                      {prettyPayload(payloadObs.output)}
                    </pre>
                  </Card>
                </div>
              ) : null}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
                  gap: 16,
                }}
              >
                <div>
                  <Waterfall
                    spans={spans}
                    totalMs={trace.duration_ms}
                    summary={`total ${trace.duration_ms.toFixed(1)} ms · forward ${(summary.forward_share * 100).toFixed(1)}% · queue ${(summary.queue_share * 100).toFixed(1)}% · ${statusLabel(trace.status)} · ${isoWithTz(trace.ts_start)}`}
                  />
                </div>
                <div>
                  <Card variant="flat" title="Scores and lifecycle" meta={`${timeline.length} entries`}>
                    {timeline.length === 0 ? (
                      <p className="lw-hint">No scores or lifecycle events for this trace.</p>
                    ) : (
                      <Timeline items={timeline} />
                    )}
                  </Card>
                  <div style={{ marginTop: 16 }}>
                    <Card variant="flat" title="Related logs" meta={`${detail.logs.length} lines`}>
                      {detail.logs.length === 0 ? (
                        <p className="lw-hint">No related log lines for this trace.</p>
                      ) : (
                        <div className="lw-log" role="log" aria-label="Related log lines">
                          {detail.logs.map((line, index) => (
                            <div
                              key={`${line.ts}-${index}`}
                              className="lw-log-line"
                            >
                              <span className="lw-log-ts">{isoWithTz(line.ts)}</span>{" "}
                              <span className={logLevelClass(line.level)}>{line.level}</span>{" "}
                              <span>{line.message}</span>
                            </div>
                          ))}
                        </div>
                      )}
                      <a
                        className="lw-btn lw-btn-ghost lw-btn-sm"
                        href={`/logs?trace_id=${encodeURIComponent(trace.id)}`}
                        style={{ marginTop: 8 }}
                      >
                        Open in Logs
                      </a>
                    </Card>
                  </div>
                </div>
              </div>
            </Card>
          )
        )}
      </section>
    </div>
  );
}
