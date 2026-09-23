/* Settings view (plan phase-6 tasks 12-16, mock section 08): server facts
 * and effective values, theme preference, retention with the disk estimate,
 * payload capture, rate-limit policy with the live bucket panel,
 * diagnostics refreshed on SSE pulse, the danger zone,
 * and the footer link to the Audit view (linked from Settings, no nav
 * group). Every mutation runs through the shared helper and every control
 * is permission-gated with an explained reason. */
"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import SectionHeader from "@/components/ui/SectionHeader";
import Card from "@/components/ui/Card";
import { Select } from "@/components/ui/Select";
import { ErrorState, Skeleton } from "@/components/ui/EmptyState";
import ServerFacts from "@/components/settings/ServerFacts";
import RetentionCard from "@/components/settings/RetentionCard";
import CaptureCard from "@/components/settings/CaptureCard";
import RateLimitsCard from "@/components/settings/RateLimitsCard";
import BucketPanel from "@/components/settings/BucketPanel";
import DiagnosticsCard from "@/components/settings/DiagnosticsCard";
import DangerZoneCard from "@/components/settings/DangerZoneCard";
import { useAuth } from "@/components/AuthProvider";
import { useMutate } from "@/lib/mutation";
import { useStream } from "@/lib/stream";
import { api, ApiError } from "@/lib/api";
import type {
  MetaResponse,
  RatelimitPolicy,
  RatelimitUsageItem,
  SettingsResponse,
} from "@/lib/api";

const THEME_KEY = "laya-theme";

function toApiError(err: unknown): ApiError | null {
  return err instanceof ApiError ? err : null;
}

export default function SettingsPage() {
  const { me } = useAuth();
  const mutate = useMutate();
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [settingsState, setSettingsState] = useState<"loading" | "ready" | "error">("loading");
  const [settingsErr, setSettingsErr] = useState<ApiError | null>(null);
  const [policy, setPolicy] = useState<RatelimitPolicy | null>(null);
  const [policyErr, setPolicyErr] = useState<ApiError | null>(null);
  const [usage, setUsage] = useState<RatelimitUsageItem[]>([]);
  const [usageErr, setUsageErr] = useState<ApiError | null>(null);
  const [meta, setMeta] = useState<MetaResponse | null>(null);
  const [theme, setTheme] = useState("system");
  const [resettingKey, setResettingKey] = useState<string | null>(null);

  const fetchSettings = useCallback(async () => {
    setSettingsState("loading");
    try {
      setSettings(await api.get<SettingsResponse>("/api/v1/settings"));
      setSettingsErr(null);
      setSettingsState("ready");
    } catch (err) {
      setSettingsErr(toApiError(err));
      setSettingsState("error");
    }
  }, []);

  const fetchPolicy = useCallback(async () => {
    try {
      setPolicy(await api.get<RatelimitPolicy>("/api/v1/ratelimits"));
      setPolicyErr(null);
    } catch (err) {
      setPolicyErr(toApiError(err));
    }
  }, []);

  const fetchUsage = useCallback(async () => {
    try {
      const page = await api.get<{ items?: RatelimitUsageItem[] | null }>(
        "/api/v1/ratelimits/usage",
      );
      setUsage(page.items ?? []);
      setUsageErr(null);
    } catch (err) {
      setUsageErr(toApiError(err));
    }
  }, []);

  const fetchMeta = useCallback(async () => {
    try {
      setMeta(await api.get<MetaResponse>("/api/v1/meta"));
    } catch {
      /* The diagnostics list renders "-" until the next pulse retries. */
    }
  }, []);

  useEffect(() => {
    void fetchSettings();
    void fetchPolicy();
    void fetchUsage();
    void fetchMeta();
    try {
      setTheme(localStorage.getItem(THEME_KEY) ?? "system");
    } catch {
      /* Storage unavailable: the select stays on system. */
    }
  }, [fetchSettings, fetchPolicy, fetchUsage, fetchMeta]);

  useStream({
    onPulse: () => {
      void fetchMeta();
      void fetchUsage();
    },
  });

  const applyTheme = (next: string) => {
    setTheme(next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* The theme still applies for this session. */
    };
    const light = window.matchMedia("(prefers-color-scheme: light)").matches;
    document.documentElement.dataset.theme =
      next === "dark" || next === "light" ? next : light ? "light" : "dark";
  };

  const resetBucket = (item: RatelimitUsageItem) => {
    const key = `${item.scope}:${item.subject}`;
    if (resettingKey) return;
    setResettingKey(key);
    void mutate(`/api/v1/ratelimits/reset`, {
      body: { subject: item.subject, scope: item.scope },
      okTitle: "Bucket reset",
      okBody: `${item.subject} (${item.scope}) cleared.`,
      refetch: fetchUsage,
    }).then(() => setResettingKey(null));
  };

  return (
    <section className="lw-sec" aria-label="Settings">
      <SectionHeader
        eyebrow="Admin"
        title="Settings"
        meta="runtime policy - preferences"
      />
      {settingsState === "loading" ? (
        <div aria-busy="true" aria-label="Loading settings" style={{ display: "grid", gap: 12 }}>
          <Skeleton height={16} width="30%" />
          <Skeleton height={120} />
          <Skeleton height={16} width="40%" />
          <Skeleton height={160} />
        </div>
      ) : settingsState === "error" || !settings ? (
        <ErrorState
          title="Failed to load settings"
          detail={settingsErr?.message}
          requestId={settingsErr?.requestId}
          onRetry={() => void fetchSettings()}
        />
      ) : (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
              gap: 16,
            }}
          >
            <Card title="Server" meta="effective values - read only">
              <ServerFacts payload={settings} />
            </Card>
            <Card title="Preferences" meta="this browser">
              <div style={{ display: "grid", gap: 8 }}>
                <Select
                  label="theme"
                  value={theme}
                  onChange={(event) => applyTheme(event.target.value)}
                >
                  <option value="system">system</option>
                  <option value="dark">dark</option>
                  <option value="light">light</option>
                </Select>
                <span className="lw-hint">system follows your OS preference live.</span>
              </div>
            </Card>
          </div>

          <Card title="Retention and sampling" meta="estimate shown before save">
            <RetentionCard payload={settings} me={me} onSaved={fetchSettings} />
          </Card>

          <Card title="Payload capture" meta="owner only - off by default">
            <CaptureCard payload={settings} me={me} onSaved={fetchSettings} />
          </Card>

          <Card title="Rate limits" meta="applies on the next request - no restart">
            {policy ? (
              <RateLimitsCard policy={policy} me={me} onSaved={fetchPolicy} />
            ) : policyErr ? (
              <ErrorState
                title="Failed to load rate limit policy"
                detail={policyErr.message}
                requestId={policyErr.requestId}
                onRetry={() => void fetchPolicy()}
              />
            ) : (
              <Skeleton height={120} />
            )}
          </Card>

          <Card title="Live buckets" meta="only subjects with usage - refreshes on pulse">
            <BucketPanel
              items={usage}
              error={usageErr}
              me={me}
              resettingKey={resettingKey}
              onReset={resetBucket}
              onRetry={() => void fetchUsage()}
            />
          </Card>

          <Card title="Diagnostics" meta="self-observability - refreshes on pulse">
            <DiagnosticsCard meta={meta} />
          </Card>

          <Card title="Danger zone" variant="flat" meta="destructive - confirm dialogs">
            <DangerZoneCard me={me} />
          </Card>

          <div className="lw-hint">
            <Link href="/audit">Audit log</Link>
            {" - append-only record of every mutation, filterable by actor, action and range."}
          </div>
        </>
      )}
    </section>
  );
}
