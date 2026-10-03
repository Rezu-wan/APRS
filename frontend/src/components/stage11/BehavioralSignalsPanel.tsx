// Stage 11C — explainable behavioral signals. Renders all 9 rows verbatim
// (no filtering, no scoring logic in React); 403 renders as a muted role line.
import { ApiError } from "../../api/client";
import type { BehavioralReport, SignalLevel } from "../../api/stage11";
import { useBehavioralSignals } from "../../hooks/useStage11";
import { ErrorState } from "../ui/ErrorState";

const LEVEL_CHIP: Record<SignalLevel, string> = {
  HIGH: "bg-red-50 text-red-700 ring-red-600/20",
  MEDIUM: "bg-amber-50 text-amber-700 ring-amber-600/20",
  LOW: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  UNKNOWN: "bg-slate-100 text-slate-600 ring-slate-500/20",
};

export function LevelChip({ level }: { level: SignalLevel }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${LEVEL_CHIP[level]}`}
    >
      {level}
    </span>
  );
}

function formatValue(value: number | string | null, unit: string | null): string {
  if (value === null) return "—";
  return unit ? `${value} ${unit}` : String(value);
}

function LoadingSkeleton() {
  return (
    <div aria-hidden="true" className="animate-pulse space-y-3 py-2">
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-4 w-2/3 rounded bg-slate-200" />
      ))}
    </div>
  );
}

function BehavioralBody({ report }: { report: BehavioralReport }) {
  const { summary } = report;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <LevelChip level={summary.overall} />
        <span className="text-xs text-slate-500 tabular-nums">
          {summary.high_count} high · {summary.medium_count} medium · {summary.unknown_count} unknown
        </span>
        <span className="font-mono text-xs text-slate-400">{report.feature_version}</span>
      </div>

      <ul className="divide-y divide-slate-100">
        {report.signals.map((signal) => (
          <li key={signal.code} className="flex items-start justify-between gap-3 py-2.5">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <LevelChip level={signal.level} />
                <span className="text-sm font-medium text-slate-800">{signal.label}</span>
              </div>
              <p className="mt-1 text-xs text-slate-500">{signal.description}</p>
            </div>
            <div className="shrink-0 text-right">
              <p className="font-mono text-sm text-slate-900 tabular-nums">
                {formatValue(signal.value, signal.unit)}
              </p>
              <p className="font-mono text-xs text-slate-400">
                {signal.window} · {signal.basis}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function BehavioralSignalsPanel({ transactionId }: { transactionId: string }) {
  const signalsQuery = useBehavioralSignals(transactionId);

  return (
    <section
      aria-labelledby="behavioral-heading"
      className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
      data-testid="behavioral-signals-panel"
    >
      <h2 id="behavioral-heading" className="text-sm font-semibold text-slate-900">
        Behavioral signals
      </h2>

      <div className="mt-4">
        {signalsQuery.isPending ? (
          <LoadingSkeleton />
        ) : signalsQuery.isError ? (
          signalsQuery.error instanceof ApiError && signalsQuery.error.status === 403 ? (
            <p className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-500">
              Behavioral signals are available to staff roles.
            </p>
          ) : (
            <ErrorState
              title="Behavioral signals unavailable"
              message={
                signalsQuery.error instanceof ApiError
                  ? signalsQuery.error.message
                  : "Could not load the behavioral signals."
              }
              onRetry={() => void signalsQuery.refetch()}
            />
          )
        ) : signalsQuery.data ? (
          <BehavioralBody report={signalsQuery.data} />
        ) : null}
      </div>
    </section>
  );
}
