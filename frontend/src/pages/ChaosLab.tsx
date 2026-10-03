import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, Loader2, Play, XCircle } from "lucide-react";
import { RoleGate } from "../components/ui/RoleGate";
import { ErrorState } from "../components/ui/ErrorState";
import { EmptyState } from "../components/ui/EmptyState";
import { useAuth } from "../context/AuthContext";
import { useChaosScenarios } from "../hooks/useStage11";
import {
  humanizeVerdict,
  runChaosScenario,
  type ChaosInvariant,
  type ChaosRunResult,
  type ChaosScenario,
} from "../api/stage11";

const VERDICT_CHIP: Record<ChaosRunResult["verdict"], string> = {
  PASS: "bg-emerald-50 text-emerald-800 ring-emerald-600/20",
  FAIL: "bg-red-50 text-red-800 ring-red-600/20",
  SKIP: "bg-slate-100 text-slate-700 ring-slate-500/20",
};

function InvariantRow({ invariant }: { invariant: ChaosInvariant }) {
  return (
    <li className="flex items-start gap-1.5 text-xs">
      {invariant.held ? (
        <CheckCircle2 aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" />
      ) : (
        <XCircle aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-600" />
      )}
      <span>
        <span
          className={
            invariant.held ? "font-medium text-emerald-800" : "font-medium text-red-700"
          }
        >
          {invariant.name}
        </span>
        {invariant.detail && (
          <span className="text-slate-500"> — {invariant.detail}</span>
        )}
      </span>
    </li>
  );
}

function ScenarioResult({ result, ranAt }: { result: ChaosRunResult; ranAt: string }) {
  return (
    <div
      data-testid={`chaos-result-${result.scenario}`}
      className="mt-3 space-y-2 rounded-md border border-slate-200 bg-slate-50 p-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ring-inset ${VERDICT_CHIP[result.verdict]}`}
        >
          {humanizeVerdict(result.verdict)}
        </span>
        {result.outcome && (
          <span className="text-xs text-slate-600">
            {result.outcome.decision} / {result.outcome.status}
            {result.outcome.blocked_reason && ` — ${result.outcome.blocked_reason}`}
          </span>
        )}
        <span className="ml-auto text-xs text-slate-400">{ranAt}</span>
      </div>

      {result.invariants.length > 0 && (
        <ul className="space-y-1" aria-label="Invariants">
          {result.invariants.map((inv) => (
            <InvariantRow key={inv.name} invariant={inv} />
          ))}
        </ul>
      )}

      {result.note && (
        <p className="text-xs text-slate-500">{result.note}</p>
      )}
    </div>
  );
}

function ScenarioCard({
  scenario,
  canAct,
  result,
  onResult,
}: {
  scenario: ChaosScenario;
  canAct: boolean;
  result: { value: ChaosRunResult; ranAt: string } | null;
  onResult: (scenario: string, value: ChaosRunResult, ranAt: string) => void;
}) {
  const mutation = useMutation({
    mutationFn: () => runChaosScenario(scenario.scenario),
    onSuccess: (value) => onResult(scenario.scenario, value, new Date().toLocaleString()),
  });

  return (
    <article
      data-testid={`chaos-card-${scenario.scenario}`}
      className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
    >
      <h3 className="font-mono text-sm font-semibold text-slate-900">{scenario.scenario}</h3>
      <p className="text-xs text-slate-500">{scenario.description}</p>

      <dl className="space-y-0.5 text-xs">
        {Object.entries(scenario.expected).map(([key, value]) => (
          <div key={key} className="flex gap-1.5">
            <dt className="font-mono text-slate-400">{key}:</dt>
            <dd className="font-mono text-slate-700">{String(value)}</dd>
          </div>
        ))}
      </dl>

      {canAct && (
        <div className="mt-auto pt-1">
          <button
            type="button"
            data-testid={`chaos-run-${scenario.scenario}`}
            disabled={mutation.isPending}
            onClick={() => mutation.mutate()}
            className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
          >
            {mutation.isPending ? (
              <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
            ) : (
              <Play aria-hidden="true" className="h-4 w-4" />
            )}
            Run
          </button>
        </div>
      )}

      {mutation.isError && (
        <p role="alert" className="text-xs text-red-600">
          {mutation.error instanceof Error
            ? mutation.error.message
            : "The scenario run failed."}
        </p>
      )}

      {result && <ScenarioResult result={result.value} ranAt={result.ranAt} />}
    </article>
  );
}

function ChaosPanel() {
  const { user } = useAuth();
  const canAct = user?.role === "SYSTEM" || user?.role === "ADMIN";
  const scenarios = useChaosScenarios();
  // Latest result per scenario — keeps each card's last run visible.
  const [results, setResults] = useState<
    Record<string, { value: ChaosRunResult; ranAt: string }>
  >({});

  function recordResult(scenario: string, value: ChaosRunResult, ranAt: string) {
    setResults((prev) => ({ ...prev, [scenario]: { value, ranAt } }));
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-lg font-semibold text-slate-900">Chaos lab</h1>
          <span className="inline-flex items-center rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-600/20">
            Simulated sandbox — no real money moves
          </span>
        </div>
        <p className="text-sm text-slate-500">
          Deterministic fault injection against the REAL recovery engine. Invariants are asserted
          after every run; nothing is ever faked.
        </p>
      </div>

      {scenarios.isPending && (
        <p className="text-sm text-slate-500" role="status">
          Loading scenarios…
        </p>
      )}
      {scenarios.isError && (
        <ErrorState
          title="Scenarios unavailable"
          message={
            scenarios.error instanceof Error
              ? scenarios.error.message
              : "Could not load the chaos scenario catalog."
          }
          error={scenarios.error}
          onRetry={() => void scenarios.refetch()}
        />
      )}
      {scenarios.data && scenarios.data.scenarios.length === 0 && (
        <EmptyState
          title="No chaos scenarios configured"
          message="The backend returned an empty scenario catalog — nothing to show honestly."
        />
      )}
      {scenarios.data && scenarios.data.scenarios.length > 0 && (
        <>
          {!canAct && (
            <p className="text-sm text-slate-500">
              Your role can view scenarios, but running them requires the SYSTEM or ADMIN role.
            </p>
          )}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {scenarios.data.scenarios.map((scenario) => (
              <ScenarioCard
                key={scenario.scenario}
                scenario={scenario}
                canAct={canAct}
                result={results[scenario.scenario] ?? null}
                onResult={recordResult}
              />
            ))}
          </div>
          <p className="text-xs text-slate-400">
            Every scenario invokes the actual pipeline (policy → safety gate → executor → sandbox
            provider → verification).
          </p>
        </>
      )}
    </div>
  );
}

export default function ChaosLab() {
  return (
    <RoleGate allowed={["SYSTEM", "ADMIN", "SUPPORT"]}>
      <ChaosPanel />
    </RoleGate>
  );
}
