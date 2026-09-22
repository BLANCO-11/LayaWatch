/* Checkpoints route (plan phase-6 tasks 7-8, acceptance criterion 3):
 * live model state from GET /api/v1/models, load/unload through the shared
 * mutation helper behind confirm dialogs with one in-flight guard, the
 * LAYA_ENGLISH_ONLY banner + disabled reason, and SSE `model` events
 * refetching the list so other clients' changes appear live. */
"use client";

import { useCallback, useEffect, useState } from "react";
import SectionHeader from "@/components/ui/SectionHeader";
import Card from "@/components/ui/Card";
import Banner from "@/components/ui/Banner";
import ModelTable from "@/components/checkpoints/ModelTable";
import { ENGLISH_ONLY_REASON } from "@/components/checkpoints/ModelActions";
import { useAuth } from "@/components/AuthProvider";
import { api, ApiError, type MetaResponse, type ModelInfo } from "@/lib/api";
import { useMutate } from "@/lib/mutation";
import { useStream } from "@/lib/stream";
import { mb } from "@/lib/format";

function toApiError(err: unknown): ApiError {
  return err instanceof ApiError
    ? err
    : new ApiError(0, "request_failed", err instanceof Error ? err.message : "Request failed.");
}

export default function CheckpointsPage() {
  const { me } = useAuth();
  const mutate = useMutate();
  const [info, setInfo] = useState<ModelInfo | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [englishOnly, setEnglishOnly] = useState(false);
  const [pendingKey, setPendingKey] = useState<string | null>(null);

  const fetchModels = useCallback(async () => {
    try {
      setInfo(await api.get<ModelInfo>("/api/v1/models"));
      setError(null);
    } catch (err) {
      setError(toApiError(err));
    }
  }, []);

  const fetchEnglishOnly = useCallback(async () => {
    try {
      const meta = await api.get<MetaResponse>("/api/v1/meta");
      setEnglishOnly(meta.config?.english_only === true);
    } catch {
      /* The banner is a UX aid; the server 409 stays authoritative. */
    }
  }, []);

  useEffect(() => {
    void fetchModels();
    void fetchEnglishOnly();
  }, [fetchModels, fetchEnglishOnly]);

  useStream({ onModel: () => void fetchModels() });

  const run = useCallback(
    async (action: "load" | "unload", name: string) => {
      if (pendingKey) return;
      const key = `${action}:${name}`;
      setPendingKey(key);
      try {
        await mutate<ModelInfo>(`/api/v1/models/${action}`, {
          body: { models: [name] },
          okTitle: action === "load" ? "Checkpoint loaded" : "Checkpoint unloaded",
          okBody: (result) =>
            result && result.loaded.length > 0
              ? `${action === "load" ? "Loaded" : "Still loaded"}: ${result.loaded.join(", ")}`
              : "No checkpoints loaded.",
          refetch: fetchModels,
        });
      } finally {
        setPendingKey(null);
      }
    },
    [mutate, pendingKey, fetchModels],
  );

  const tableState = error ? "error" : info ? "ready" : "loading";

  return (
    <section
      className="lw-sec"
      aria-label="Checkpoints"
      style={{ display: "flex", flexDirection: "column", gap: 16 }}
    >
      <SectionHeader eyebrow="Operate" title="Checkpoints" meta="model inventory" />
      {englishOnly ? (
        <Banner tone="warn">{ENGLISH_ONLY_REASON}</Banner>
      ) : null}
      <Card
        variant="flat"
        meta={
          info
            ? `${info.loaded.length} loaded / ${info.available.length} available`
            : undefined
        }
        footer={
          info ? (
            <span className="mono">
              device {info.device} &middot; rss {mb(info.rss_mb)}
            </span>
          ) : undefined
        }
      >
        <ModelTable
          state={tableState}
          info={info}
          error={error}
          me={me}
          englishOnly={englishOnly}
          pendingKey={pendingKey}
          onRun={(action, name) => void run(action, name)}
          onRetry={() => {
            setError(null);
            void fetchModels();
          }}
        />
      </Card>
    </section>
  );
}
