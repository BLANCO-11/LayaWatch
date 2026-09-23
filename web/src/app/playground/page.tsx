/* Playground: posts a JSON state and questions object to the live
 * POST /predict endpoint and renders the answers, routing decision and timing.
 * Engine contract: docs/api-reference.md section 4 (state and questions are
 * both JSON objects; model is an optional override, empty means router auto).
 */
"use client";

import { useEffect, useState } from "react";
import SectionHeader from "@/components/ui/SectionHeader";
import EmptyState, { ErrorState } from "@/components/ui/EmptyState";
import Card from "@/components/ui/Card";
import Chip from "@/components/ui/Chip";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import CopyField from "@/components/ui/CopyField";
import KeyValueList, { type KeyValue } from "@/components/ui/KeyValueList";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { api, ApiError } from "@/lib/api";
import type { MetaResponse } from "@/lib/api";

type RunState = "idle" | "running" | "done" | "error";

interface PredictResult {
  answers: Record<string, unknown>;
  model: string;
  route_reason?: string;
  lang?: string;
  usage?: unknown;
}

/* Canonical fixtures from scripts/smoke_laya.py: the engine's question specs
 * require a `type` key (laya agent.py reads qdef["type"]). */
const DEFAULT_STATE = `{
  "from": "user@acme.com",
  "subject": "Duplicate charge on invoice #4411",
  "body": "Hi, we were billed twice for March. Please refund the duplicate today or we will cancel our plan."
}`;
const DEFAULT_QUESTIONS = `{
  "department": {
    "type": "choice",
    "instructions": "Which department should handle this request?",
    "criteria": {
      "billing": "invoices, payments, refunds",
      "technical": "bugs, outages, system errors",
      "sales": "pricing, new contracts",
      "other": "everything else"
    }
  },
  "urgency": {
    "type": "score",
    "instructions": "How urgent is this request?",
    "criteria": ["not urgent", "soon", "critical deadline or blocking issue"]
  },
  "churn_risk": {
    "type": "noul",
    "instructions": "Does the user threaten to cancel or leave?"
  }
}`;

function answerRows(answers: Record<string, unknown>): KeyValue[] {
  return Object.entries(answers).map(([question, answer]) => {
    const isObject = answer !== null && typeof answer === "object";
    /* Confidence arrives as a 0..1 float (engine contract,
     * scripts/smoke_laya.py); render a whole-percent chip and fall back to a
     * muted dash when the field is missing or not a finite number - never
     * NaN or undefined. */
    const raw = isObject && "confidence" in answer ? answer.confidence : undefined;
    const percent =
      typeof raw === "number" && Number.isFinite(raw)
        ? `${Math.round(raw * 100)}%`
        : null;
    return {
      key: question,
      value: (
        <span
          style={{
            display: "inline-flex",
            gap: 8,
            alignItems: "center",
            flexWrap: "wrap",
            maxWidth: "100%",
          }}
        >
          <span>{isObject ? JSON.stringify(answer) : String(answer)}</span>
          <Chip
            name="confidence"
            value={percent ?? <span className="lw-hint">-</span>}
          />
        </span>
      ),
    };
  });
}

export default function PlaygroundPage() {
  const [stateText, setStateText] = useState(DEFAULT_STATE);
  const [questionsText, setQuestionsText] = useState(DEFAULT_QUESTIONS);
  const [model, setModel] = useState("");
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const [state, setState] = useState<RunState>("idle");
  const [result, setResult] = useState<PredictResult | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [fieldErrors, setFieldErrors] = useState<{ state?: string; questions?: string }>({});
  const [detail, setDetail] = useState<string | undefined>(undefined);
  const [requestId, setRequestId] = useState<string | undefined>(undefined);

  useEffect(() => {
    api
      .get<MetaResponse>("/api/v1/meta")
      .then((meta) => setModelOptions(meta.config?.models ?? []))
      .catch(() => {});
  }, []);

  const run = () => {
    const nextErrors: { state?: string; questions?: string } = {};
    let parsedState: unknown;
    let parsedQuestions: unknown;
    try {
      parsedState = JSON.parse(stateText);
    } catch {
      nextErrors.state = "State must be valid JSON.";
    }
    try {
      parsedQuestions = JSON.parse(questionsText);
    } catch {
      nextErrors.questions = "Questions must be valid JSON.";
    }
    if (!nextErrors.state && (parsedState === null || typeof parsedState !== "object" || Array.isArray(parsedState))) {
      nextErrors.state = "State must be a JSON object.";
    }
    if (!nextErrors.questions && (parsedQuestions === null || typeof parsedQuestions !== "object" || Array.isArray(parsedQuestions))) {
      nextErrors.questions = "Questions must be a JSON object.";
    }
    setFieldErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    setState("running");
    setDetail(undefined);
    setRequestId(undefined);
    const startedAt = Date.now();
    api
      .post<PredictResult>("/predict", {
        state: parsedState,
        questions: parsedQuestions,
        ...(model ? { model } : {}),
      })
      .then((payload) => {
        setResult(payload);
        setElapsedMs(Date.now() - startedAt);
        setState("done");
      })
      .catch((err: unknown) => {
        setElapsedMs(Date.now() - startedAt);
        if (err instanceof ApiError) {
          setDetail(`${err.status} ${err.code}: ${err.message}`);
          setRequestId(err.requestId);
        } else {
          setDetail("The server could not be reached. Check that the process is running.");
        }
        setResult(null);
        setState("error");
      });
  };

  return (
    <section className="lw-sec" aria-label="Playground">
      <SectionHeader
        eyebrow="Operate"
        title="Playground"
        meta="runs against live POST /predict"
      />
      <Card variant="flat">
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
            gap: 16,
            marginBottom: 16,
          }}
        >
          <Select
            label="model"
            size="sm"
            value={model}
            onChange={(event) => setModel(event.target.value)}
          >
            <option value="">auto (router)</option>
            {modelOptions.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </Select>
          <div style={{ display: "flex", alignItems: "flex-end", gap: 12 }}>
            <Button variant="primary" onClick={run} loading={state === "running"}>
              Run
            </Button>
            {state === "done" && result ? (
              <Badge tone="ok">{`200 · ${elapsedMs} ms`}</Badge>
            ) : state === "error" ? (
              <Badge tone="err">{`error · ${elapsedMs} ms`}</Badge>
            ) : null}
          </div>
        </div>
        <Textarea
          label="state"
          rows={4}
          value={stateText}
          error={fieldErrors.state}
          onChange={(event) =>
            setStateText(event.target.value)
          }
        />
        <div style={{ height: 12 }} />
        <Textarea
          label="questions"
          rows={5}
          value={questionsText}
          error={fieldErrors.questions}
          onChange={(event) =>
            setQuestionsText(event.target.value)
          }
        />
      </Card>

      <div style={{ marginTop: 24 }}>
        {state === "error" ? (
          <ErrorState
            title="Run failed"
            detail={detail}
            requestId={requestId}
            onRetry={run}
          />
        ) : state === "done" && result ? (
          <Card
            title="Answers"
            meta={
              <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
                <Badge tone="ok">{`200 · ${elapsedMs} ms`}</Badge>
              </span>
            }
          >
            <div className="lw-chips" style={{ marginBottom: 12 }}>
              <Chip name="model" value={result.model} />
              {result.route_reason ? (
                <Chip name="route_reason" value={result.route_reason} />
              ) : null}
              {result.lang ? <Chip name="lang" value={result.lang} /> : null}
            </div>
            <KeyValueList items={answerRows(result.answers ?? {})} />
            {requestId ? (
              <div style={{ marginTop: 12 }}>
                <CopyField label="Request id" value={requestId} />
              </div>
            ) : null}
          </Card>
        ) : (
          <Card variant="flat">
            <EmptyState
              title="No run yet"
              body="Adjust the state and questions, then Run to call /predict."
            />
          </Card>
        )}
      </div>
    </section>
  );
}
