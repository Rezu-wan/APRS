import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Download, Loader2 } from "lucide-react";
import { RoleGate } from "../components/ui/RoleGate";
import { ErrorState } from "../components/ui/ErrorState";
import { useAuth } from "../context/AuthContext";
import {
  runPolicySimulation,
  type PolicySimulationResult,
  PolicySimulationRun,
  PolicyVersion,
  SimulatorRunRequest,
} from "../api/stage11";

const POLICY_OPTIONS: Array<{
  version: PolicyVersion;
  label: string;
}> = [
  { version: "autonomous-v1", label: "Active policy" },
  { version: "manual-only-baseline", label: "Research baseline (never releases)" },
  {
    version: "autonomous-v2-experimental",
    label: "Experimental: MEDIUM risk needs confidence ≥ 0.6",
  },
];

/** Shorten long identifiers for compact mono display. */
function short(value: string, max = 12): string {
  return value.length <= max ? value : `${value.slice(0, max)}…`;
}

function PolicyCard({ result }: { result: PolicySimulationResult }) {
  return (
    <article className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-mono text-sm font-semibold text-slate-900">
          {result.policy_version}
        </h3>
        {result.experimental && (
          <span className="inline-flex items-center rounded-full bg-purple-50 px-2 py-0.5 text-xs font-medium text-purple-700 ring-1 ring-inset ring-purple-600/20">
            experimental
          </span>
        )}
      </div>
      <p className="text-xs text-slate-500">{result.description}</p>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs text-slate-700 sm:grid-cols-4">
        <div>
          <dt className="text-slate-400">Evaluated</dt>
          <dd className="font-medium">{result.transactions_evaluated}</dd>
        </div>
        <div>
          <dt className="text-slate-400">Release</dt>
          <dd className="font-medium">{result.would_release}</dd>
        </div>
        <div>
          <dt className="text-slate-400">Block</dt>
          <dd className="font-medium">{result.would_block}</dd>
        </div>
        <div>
          <dt className="text-slate-400">Manual review</dt>
          <dd className="font-medium">{result.would_manual_review}</dd>
        </div>
        <div>
          <dt className="text-slate-400">No action</dt>
          <dd className="font-medium">{result.would_no_action}</dd>
        </div>
        <div>
          <dt className="text-slate-400">Gate vetoes</dt>
          <dd className="font-medium">{result.gate_vetoes}</dd>
        </div>
        <div>
          <dt className="text-slate-400">Provider calls avoided</dt>
          <dd className="font-medium">{result.provider_calls_avoided}</dd>
        </div>
        <div>
          <dt className="text-slate-400">Latency avg</dt>
          <dd className="font-medium">{result.decision_latency_ms_avg} ms</dd>
        </div>
        {result.false_recovery !== null && (
          <div>
            <dt className="text-slate-400">False recovery</dt>
            <dd className="font-medium">{result.false_recovery}</dd>
          </div>
        )}
        {result.missed_recovery !== null && (
          <div>
            <dt className="text-slate-400">Missed recovery</dt>
            <dd className="font-medium">{result.missed_recovery}</dd>
          </div>
        )}
      </dl>

      {result.decisions.length > 0 && (
        <details className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
          <summary className="cursor-pointer text-xs font-medium text-slate-700">
            Per-transaction decisions ({result.decisions.length})
          </summary>
          <ul className="mt-2 space-y-1">
            {result.decisions.map((d) => (
              <li
                key={d.transaction_id}
                className="flex flex-wrap items-center gap-2 text-xs text-slate-600"
              >
                <span className="font-mono">{d.transaction_id}</span>
                <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700 ring-1 ring-inset ring-slate-500/20">
                  {d.action}
                </span>
                {d.blocked_reason && <span>blocked: {d.blocked_reason}</span>}
                {d.gate_vetoed && (
                  <span className="font-medium text-amber-700">gate veto</span>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
    </article>
  );
}

function SimulationResults({ run }: { run: PolicySimulationRun }) {
  return (
    <section aria-label="Simulation results" className="space-y-4">
      <p className="text-xs text-slate-500" data-testid="run-meta">
        <span className="font-mono">{short(run.run_id)}</span>
        {" · "}
        {run.generated_at}
        {" · dataset "}
        <span className="font-mono">{short(run.dataset.dataset_fingerprint)}</span>
        {" · "}
        affects live policy: no
        {!run.ground_truth_available && (
          <span className="text-amber-700">
            {" "}
            — arbitrary corpus: false/missed recovery not scored
          </span>
        )}
      </p>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {run.policies.map((p) => (
          <PolicyCard key={p.policy_version} result={p} />
        ))}
      </div>

      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-slate-200 text-xs">
          <caption className="sr-only">Per-metric comparison across policies</caption>
          <thead className="bg-slate-50">
            <tr>
              <th scope="col" className="px-3 py-2 text-left font-medium text-slate-500">
                Metric
              </th>
              {run.policies.map((p) => (
                <th
                  key={p.policy_version}
                  scope="col"
                  className="px-3 py-2 text-left font-mono font-medium text-slate-700"
                >
                  {p.policy_version}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {run.comparison.map((row) => (
              <tr key={row.metric}>
                <td className="px-3 py-1.5 font-medium text-slate-700">{row.metric}</td>
                {run.policies.map((p) => {
                  const value = row.per_policy[p.policy_version];
                  return (
                    <td key={p.policy_version} className="px-3 py-1.5 text-slate-600">
                      {value === undefined ? "—" : String(value)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td
                colSpan={run.policies.length + 1}
                className="px-3 py-2 text-slate-400"
              >
                The simulator reports measurable differences only; it does not rank policies.
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}

function SimulatorPanel() {
  const { user } = useAuth();
  const canAct = user?.role === "SYSTEM" || user?.role === "ADMIN";

  const [selected, setSelected] = useState<Set<PolicyVersion>>(
    new Set(["autonomous-v1"])
  );
  const [datasetSource, setDatasetSource] = useState<"demo" | "custom">("demo");
  const [customIds, setCustomIds] = useState("");
  const [run, setRun] = useState<PolicySimulationRun | null>(null);
  const [exported, setExported] = useState(false);

  const mutation = useMutation({
    mutationFn: (request: SimulatorRunRequest) => runPolicySimulation(request),
    onSuccess: (result) => {
      setRun(result);
      setExported(false);
    },
  });

  function togglePolicy(version: PolicyVersion, checked: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) next.add(version);
      else next.delete(version);
      return next;
    });
  }

  function handleRun() {
    const ids = customIds
      .split(/[\s,]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    mutation.mutate({
      policies: Array.from(selected),
      source: datasetSource === "demo" ? { demo: true } : { demo: false, transaction_ids: ids },
    });
  }

  function handleExport() {
    if (!run) return;
    const blob = new Blob([JSON.stringify(run, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `stage11-policy-simulation-${run.run_id}.json`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    setExported(true);
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-lg font-semibold text-slate-900">Policy simulator</h1>
          <span className="inline-flex items-center rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-medium text-emerald-800 ring-1 ring-inset ring-emerald-600/20">
            No simulation can affect the live policy or the sandbox ledger
          </span>
          <span className="inline-flex items-center rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-600/20">
            Simulated sandbox — no real money moves
          </span>
        </div>
        <p className="text-sm text-slate-500">
          Replays stored evidence through versioned policies — policy + safety gate only; the
          executor, provider and Digital Twin are never invoked.
        </p>
      </div>

      {canAct ? (
        <section
          aria-label="Simulation controls"
          className="space-y-4 rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
        >
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium text-slate-900">Policies</legend>
            {POLICY_OPTIONS.map(({ version, label }) => (
              <label key={version} className="flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  data-testid={`policy-${version}`}
                  checked={selected.has(version)}
                  onChange={(e) => togglePolicy(version, e.target.checked)}
                  className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus-visible:ring-2 focus-visible:ring-indigo-500"
                />
                <span className="font-mono text-xs">{version}</span>
                <span className="text-slate-600">— {label}</span>
              </label>
            ))}
          </fieldset>

          <fieldset className="space-y-2">
            <legend className="text-sm font-medium text-slate-900">Dataset</legend>
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="radio"
                name="dataset-source"
                value="demo"
                checked={datasetSource === "demo"}
                onChange={() => setDatasetSource("demo")}
                className="h-4 w-4 border-slate-300 text-indigo-600 focus-visible:ring-2 focus-visible:ring-indigo-500"
              />
              Demo corpus S1–S6
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="radio"
                name="dataset-source"
                value="custom"
                checked={datasetSource === "custom"}
                onChange={() => setDatasetSource("custom")}
                className="h-4 w-4 border-slate-300 text-indigo-600 focus-visible:ring-2 focus-visible:ring-indigo-500"
              />
              Custom transaction ids
            </label>
            {datasetSource === "custom" && (
              <textarea
                data-testid="custom-ids"
                rows={2}
                placeholder="TXN-1, TXN-2 …"
                value={customIds}
                onChange={(e) => setCustomIds(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-xs text-slate-700 shadow-sm focus:border-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
              />
            )}
          </fieldset>

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              data-testid="run-simulation"
              disabled={mutation.isPending || selected.size === 0}
              onClick={handleRun}
              className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
            >
              {mutation.isPending ? (
                <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
              ) : null}
              Run simulation
            </button>
            {run && (
              <button
                type="button"
                data-testid="export-results"
                onClick={handleExport}
                className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
              >
                <Download aria-hidden="true" className="h-4 w-4" />
                Export results
              </button>
            )}
            {exported && (
              <span role="status" data-testid="export-done" className="text-xs text-emerald-700">
                Results downloaded.
              </span>
            )}
          </div>
        </section>
      ) : (
        <p className="text-sm text-slate-500">
          Your role can view simulation results, but running simulations requires the SYSTEM or
          ADMIN role.
        </p>
      )}

      {mutation.isError && (
        <ErrorState
          title="Simulation failed"
          message={
            mutation.error instanceof Error
              ? mutation.error.message
              : "The simulation run could not be completed."
          }
          error={mutation.error}
          onRetry={() => mutation.reset()}
        />
      )}

      {run && <SimulationResults run={run} />}
    </div>
  );
}

export default function PolicySimulator() {
  return (
    <RoleGate allowed={["SYSTEM", "ADMIN", "SUPPORT"]}>
      <SimulatorPanel />
    </RoleGate>
  );
}
