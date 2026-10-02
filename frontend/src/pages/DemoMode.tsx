import { useState } from "react";
import { Link } from "react-router-dom";
import { Loader2, RefreshCcw } from "lucide-react";
import { RoleGate } from "../components/ui/RoleGate";
import { ErrorState } from "../components/ui/ErrorState";
import { EmptyState } from "../components/ui/EmptyState";
import { useAuth } from "../context/AuthContext";
import {
  humanizeBlockedReason,
  type DemoScenarioKey,
  type DemoScenarioStatus,
} from "../api/demo";
import {
  useDemoScenarios,
  useInjectLateSettlement,
  usePrepareScenario,
  useResetDemo,
} from "../hooks/useDemo";

const EXPECTED_CHIP_LABELS: Array<[keyof DemoScenarioStatus["expected"], string]> = [
  ["anomaly", "Anomaly"],
  ["risk_level", "Risk"],
  ["decision", "Decision"],
  ["status", "Status"],
];

function StatusLine({ scenario }: { scenario: DemoScenarioStatus }) {
  if (!scenario.exists) {
    return <p className="text-xs text-slate-500">Not yet prepared — no transaction in the sandbox.</p>;
  }
  return (
    <div className="space-y-1 text-xs text-slate-600">
      <p>
        <span className="font-medium text-slate-800">{scenario.transaction_id}</span>
        {" · "}
        {scenario.prepared ? "prepared" : "no evidence"}
        {scenario.processed ? " · processed" : ""}
        {scenario.current_state ? ` · state: ${scenario.current_state}` : ""}
      </p>
      {scenario.risk && (
        <p>
          Risk: {scenario.risk.anomaly_type} ({scenario.risk.risk_level})
        </p>
      )}
      {scenario.recovery && (
        <p>
          Recovery: {scenario.recovery.decision} / {scenario.recovery.status}
          {scenario.recovery.blocked_reason && (
            <> — {humanizeBlockedReason(scenario.recovery.blocked_reason)}</>
          )}
        </p>
      )}
    </div>
  );
}

function ScenarioCard({
  scenario,
  canAct,
}: {
  scenario: DemoScenarioStatus;
  canAct: boolean;
}) {
  const prepare = usePrepareScenario();
  const inject = useInjectLateSettlement();
  const isS5 = scenario.key === "S5";

  return (
    <article className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center gap-2">
        <span className="inline-flex h-7 w-9 items-center justify-center rounded-md bg-indigo-600 text-xs font-bold text-white">
          {scenario.key}
        </span>
        <h3 className="text-sm font-semibold text-slate-900">{scenario.title}</h3>
      </div>
      <p className="text-xs text-slate-500">{scenario.subtitle}</p>
      <p className="text-sm text-slate-700">{scenario.story}</p>

      <ul className="flex flex-wrap gap-1.5" aria-label={`Expected outcome for ${scenario.key}`}>
        {EXPECTED_CHIP_LABELS.map(([field, label]) => (
          <li
            key={field}
            className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700 ring-1 ring-inset ring-slate-500/20"
          >
            <span className="text-slate-400">{label}:</span>
            {scenario.expected[field]}
          </li>
        ))}
      </ul>

      <StatusLine scenario={scenario} />

      <div className="mt-auto flex flex-wrap items-center gap-2 pt-1">
        {canAct && (
          <button
            type="button"
            data-testid={`prepare-${scenario.key}`}
            disabled={prepare.isPending}
            onClick={() => prepare.mutate(scenario.key as DemoScenarioKey)}
            className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
          >
            {prepare.isPending ? (
              <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
            ) : null}
            Prepare
          </button>
        )}
        {canAct && isS5 && (
          <button
            type="button"
            data-testid="inject-S5"
            disabled={inject.isPending || !scenario.prepared}
            title={
              scenario.prepared
                ? "Inject a late SETTLEMENT_CONFIRMED event after preparation"
                : "Prepare S5 first — the late settlement only makes sense after the initial evidence"
            }
            onClick={() => inject.mutate("S5")}
            className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
          >
            {inject.isPending ? (
              <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
            ) : null}
            Inject late settlement
          </button>
        )}
        {scenario.exists && (
          <Link
            to={`/transactions/${encodeURIComponent(scenario.transaction_id)}`}
            className="ml-auto text-sm font-medium text-indigo-600 hover:text-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
          >
            Open transaction →
          </Link>
        )}
      </div>
    </article>
  );
}

function DemoPanel() {
  const { user } = useAuth();
  const canAct = user?.role === "SYSTEM" || user?.role === "ADMIN";
  const scenarios = useDemoScenarios();
  const reset = useResetDemo();
  const [confirmingReset, setConfirmingReset] = useState(false);
  const [resetDone, setResetDone] = useState(false);

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-lg font-semibold text-slate-900">Hackathon demo mode</h1>
          <span className="mt-1 inline-flex items-center rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-600/20">
            Simulated sandbox — no real money moves
          </span>
        </div>
        {canAct && (
          <div className="shrink-0">
            {confirmingReset ? (
              <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
                <span>Reset sandbox demo state? This affects simulated data only.</span>
                <button
                  type="button"
                  data-testid="reset-confirm"
                  disabled={reset.isPending}
                  onClick={() =>
                    reset.mutate(undefined, {
                      onSuccess: () => {
                        setResetDone(true);
                        setConfirmingReset(false);
                      },
                    })
                  }
                  className="inline-flex items-center gap-1.5 rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500 focus-visible:ring-offset-2"
                >
                  {reset.isPending ? (
                    <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
                  ) : null}
                  Confirm
                </button>
                <button
                  type="button"
                  data-testid="reset-cancel"
                  onClick={() => setConfirmingReset(false)}
                  className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                {resetDone && (
                  <span role="status" data-testid="reset-success" className="text-xs text-emerald-700">
                    Demo state reset — sandbox data re-prepared.
                  </span>
                )}
                <button
                  type="button"
                  data-testid="reset-demo"
                  onClick={() => {
                    setResetDone(false);
                    setConfirmingReset(true);
                  }}
                  className="inline-flex items-center gap-1.5 rounded-md border border-red-200 bg-white px-3 py-1.5 text-sm font-medium text-red-700 shadow-sm hover:bg-red-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500 focus-visible:ring-offset-2"
                >
                  <RefreshCcw aria-hidden="true" className="h-4 w-4" />
                  Reset demo
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {!canAct && (
        <p className="text-sm text-slate-500">
          Your role can view demo scenarios, but preparing, injecting, and resetting require the
          SYSTEM or ADMIN role.
        </p>
      )}

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
              : "Could not load the demo scenario list."
          }
          error={scenarios.error}
          onRetry={() => void scenarios.refetch()}
        />
      )}
      {scenarios.data && scenarios.data.scenarios.length === 0 && (
        <EmptyState
          title="No demo scenarios configured"
          message="The backend returned an empty scenario catalog — nothing to show honestly."
        />
      )}
      {scenarios.data && scenarios.data.scenarios.length > 0 && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {scenarios.data.scenarios.map((scenario) => (
            <ScenarioCard key={scenario.key} scenario={scenario} canAct={canAct} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function DemoMode() {
  return (
    <RoleGate allowed={["SYSTEM", "ADMIN", "SUPPORT"]}>
      <DemoPanel />
    </RoleGate>
  );
}
