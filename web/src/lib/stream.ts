/* SSE client for GET /api/v1/stream.
 * Contract: docs/api-reference.md section 8.
 * Events: hello, pulse, trace, log, model, ping. Reconnects with
 * Last-Event-ID replay and capped backoff; the shell live pill renders the
 * connection state (connecting, live, reconnecting, polling, offline).
 *
 * Polling fallback (phase 5 plan task 11): when a consumer registers `onPoll`
 * and the stream fails repeatedly, the client polls metrics/summary, traces and
 * logs at the stream tick cadence and reports status "polling" (the pill reads
 * `polling 3s`). A successful reconnect stops the poll and returns to "connected"
 * without a page reload; the backoff cap keeps recovery inside 15 seconds.
 */
"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type {
  LogEntry,
  MetricsSummary,
  Paged,
  StreamHello,
  StreamPulse,
  TraceSummary,
} from "@/lib/api";
import type { TimerId } from "@/lib/filters";

export type StreamStatus = "connecting" | "connected" | "reconnecting" | "polling" | "offline";

export interface StreamPollData {
  summary: MetricsSummary | null;
  traces: Paged<TraceSummary> | null;
  logs: Paged<LogEntry> | null;
}

export interface StreamHandlers {
  onHello?: (hello: StreamHello) => void;
  onPulse?: (pulse: StreamPulse) => void;
  onTrace?: (trace: TraceSummary) => void;
  onLog?: (log: LogEntry) => void;
  onModel?: (model: { loaded: string[]; action: string }) => void;
  /** Polling-fallback tick: fired while the stream is down, only if registered. */
  onPoll?: (data: StreamPollData) => void;
}

/* Cap keeps the worst-case reconnect delay well under the 15 s recovery budget. */
const MAX_BACKOFF_MS = 5000;
/** Failures before the polling fallback engages (first retry lands at 1 s). */
const FALLBACK_AFTER_FAILURES = 2;
const DEFAULT_TICK_S = 3;

export function useStream(handlers: StreamHandlers, enabled = true): StreamStatus {
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;
  const lastId = useRef<string | null>(null);
  const attempts = useRef(0);
  const tickS = useRef(DEFAULT_TICK_S);

  useEffect(() => {
    if (!enabled || typeof EventSource === "undefined") {
      if (!enabled) setStatus("offline");
      return;
    }
    let source: EventSource | null = null;
    let closed = false;
    let backoffTimer: TimerId;
    let pollTimer: TimerId;
    let polling = false;

    const pollOnce = () => {
      const handler = handlersRef.current.onPoll;
      if (!handler) return;
      void Promise.allSettled([
        api.get<MetricsSummary>("/api/v1/metrics/summary"),
        api.get<Paged<TraceSummary>>("/api/v1/traces?limit=10"),
        api.get<Paged<LogEntry>>("/api/v1/logs?limit=50"),
      ]).then(([summary, traces, logs]) => {
        if (closed || !polling) return;
        handler({
          summary: summary.status === "fulfilled" ? summary.value : null,
          traces: traces.status === "fulfilled" ? traces.value : null,
          logs: logs.status === "fulfilled" ? logs.value : null,
        });
      });
    };

    const startPolling = () => {
      if (polling) return;
      polling = true;
      setStatus("polling");
      if (!handlersRef.current.onPoll) return;
      pollOnce();
      pollTimer = window.setInterval(pollOnce, tickS.current * 1000);
    };

    const stopPolling = () => {
      polling = false;
      clearInterval(pollTimer);
    };

    const connect = () => {
      if (closed) return;
      setStatus(attempts.current === 0 ? "connecting" : "reconnecting");
      const url = new URL("/api/v1/stream", window.location.origin);
      if (lastId.current) url.searchParams.set("last_event_id", lastId.current);
      const next = new EventSource(url.toString());
      source = next;

      const listen = (event: string, fn: (data: unknown) => void) => {
        next.addEventListener(event, (msg) => {
          const ev = msg as MessageEvent;
          if (ev.lastEventId) lastId.current = ev.lastEventId;
          let data: unknown = null;
          try {
            data = JSON.parse(ev.data);
          } catch {
            data = null;
          }
          fn(data);
        });
      };

      listen("hello", (data) => {
        const hello = data as StreamHello;
        if (typeof hello?.tick === "number" && hello.tick > 0) tickS.current = hello.tick;
        handlersRef.current.onHello?.(hello);
      });
      listen("pulse", (data) => handlersRef.current.onPulse?.(data as StreamPulse));
      listen("trace", (data) => handlersRef.current.onTrace?.(data as TraceSummary));
      listen("log", (data) => handlersRef.current.onLog?.(data as LogEntry));
      listen("model", (data) =>
        handlersRef.current.onModel?.(data as { loaded: string[]; action: string }),
      );
      listen("ping", () => {});

      next.onopen = () => {
        attempts.current = 0;
        stopPolling();
        setStatus("connected");
      };

      next.onerror = () => {
        next.close();
        if (source === next) source = null;
        if (closed) return;
        attempts.current += 1;
        if (attempts.current >= FALLBACK_AFTER_FAILURES) startPolling();
        else setStatus("reconnecting");
        const backoff = Math.min(1000 * 2 ** (attempts.current - 1), MAX_BACKOFF_MS);
        backoffTimer = window.setTimeout(connect, backoff);
      };
    };

    connect();
    return () => {
      closed = true;
      clearTimeout(backoffTimer);
      stopPolling();
      source?.close();
    };
  }, [enabled]);

  return status;
}

export const STREAM_STATUS_LABEL: Record<StreamStatus, string> = {
  connecting: "connecting",
  connected: "live",
  reconnecting: "reconnecting",
  polling: "polling",
  offline: "offline",
};
