import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Info, Search } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { useStats } from "../hooks/useQueries";
import { ApiError } from "../api/client";
import { ErrorState } from "../components/ui/ErrorState";
import { EmptyState } from "../components/ui/EmptyState";
import { StatusBadge } from "../components/transaction/StatusBadge";
import { humanizeAnomaly, type TransactionState } from "../types/api";

/** Fixed state order; only states present in by_state are rendered (zero fabrication). */
const STATE_ORDER: TransactionState[] = [
  "INITIATED",
  "PROCESSING",
  "SUCCESS",
  "FAILED",
  "STALLED",
  "RISK_ASSESSED",
  "RECOVERY_PENDING",
  "LIMIT_RELEASED",
  "MANUAL_REVIEW",
  "RECOVERY_REJECTED",
];

const DECISION_LABELS = [
  ["LIMIT_RELEASED", "Limits released"],
  ["MANUAL_REVIEW", "Manual reviews"],
  ["RECOVERY_REJECTED", "Recovery rejected"],
] as const;

/** Top-N anomaly-type chips for the additive risk-assessment stats block. */
function topAnomalyTypes(
  byAnomalyType: Record<string, number>,
  n = 3
): Array<[string, number]> {
  return Object.entries(byAnomalyType)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, n);
}

const percentFormat = new Intl.NumberFormat("en", { style: "percent", maximumFractionDigits: 0 });

function QuickSearch() {
  const [value, setValue] = useState("");
  const navigate = useNavigate();

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const id = value.trim();
    if (id.length > 0 && id.length <= 64) {
      navigate(`/transactions/${encodeURIComponent(id)}`);
    }
  }

  return (
    <form onSubmit={handleSubmit} role="search" className="w-full max-w-md">
      <label htmlFor="dashboard-quick-search" className="sr-only">
        Look up a transaction by ID
      </label>
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
          />
          <input
            id="dashboard-quick-search"
            type="text"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="Transaction ID…"
            maxLength={64}
            className="w-full rounded-md border border-slate-300 bg-white py-2 pl-9 pr-3 text-sm text-slate-900 placeholder:text-slate-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          />
        </div>
        <button
          type="submit"
          disabled={value.trim().length === 0}
          className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
        >
          Look up
        </button>
      </div>
    </form>
  );
}

function SkeletonCard() {
  return (
    <div
      aria-hidden="true"
      className="animate-pulse rounded-lg border border-slate-200 bg-white p-6 shadow-sm"
    >
      <div className="h-3 w-24 rounded bg-slate-200" />
      <div className="mt-3 h-7 w-16 rounded bg-slate-200" />
    </div>
  );
}

function StateRow({ state, count, total }: { state: string; count: number; total: number }) {
  return (
    <li className="flex items-center gap-3">
      <span className="w-36 shrink-0 sm:w-44">
        <StatusBadge state={state} />
      </span>
      <span
        className="h-2 min-w-1 rounded-full bg-indigo-500"
        style={{ width: `${Math.max((count / total) * 100, 1)}%` }}
        aria-hidden="true"
      />
      <span className="ml-auto flex shrink-0 items-baseline gap-2 text-sm tabular-nums">
        <span className="font-medium text-slate-900">{count.toLocaleString()}</span>
        <span className="text-xs text-slate-500">{percentFormat.format(count / total)}</span>
      </span>
    </li>
  );
}

export default function Dashboard() {
  const { user } = useAuth();
  const stats = useStats();
  const isCustomer = user?.role === "CUSTOMER";

  if (isCustomer) {
    return (
      <div className="mx-auto max-w-3xl space-y-6">
        <div
          role="note"
          className="flex items-start gap-3 rounded-lg border border-indigo-100 bg-indigo-50 p-4 text-sm text-indigo-900"
        >
          <Info aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-indigo-600" />
          <p>
            You are signed in with a customer API key. Aggregate statistics are only
            available to system, admin, and support roles — use the transaction search
            to look up your own transactions.
          </p>
        </div>
        {stats.isError && (
          <ErrorState
            title="Statistics unavailable"
            message={
              stats.error instanceof ApiError
                ? stats.error.message
                : "Your role cannot view platform statistics."
            }
            onRetry={() => void stats.refetch()}
          />
        )}
      </div>
    );
  }

  if (stats.isPending) {
    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3" aria-hidden="true">
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
      </div>
    );
  }

  if (stats.isError) {
    return (
      <ErrorState
        message={
          stats.error instanceof ApiError
            ? stats.error.message
            : "Could not load platform statistics."
        }
        onRetry={() => void stats.refetch()}
      />
    );
  }

  const data = stats.data;

  if (data.total === 0) {
    return (
      <div className="space-y-6">
        <QuickSearch />
        <EmptyState
          title="No transactions yet"
          message="Once transactions are ingested, platform statistics will appear here."
        />
      </div>
    );
  }

  const presentStates = STATE_ORDER.filter((state) => (data.by_state[state] ?? 0) > 0);
  // States the backend added that are not in the fixed order — still show real counts.
  const extraStates = Object.entries(data.by_state)
    .filter(([state, count]) => count > 0 && !STATE_ORDER.includes(state as TransactionState))
    .sort((a, b) => b[1] - a[1]);

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <h1 className="text-lg font-semibold text-slate-900">Platform overview</h1>
        <QuickSearch />
      </div>

      {/* Additive Stage 7 block — rendered only when the backend provides it */}
      {data.risk_assessments && (
        <section aria-labelledby="risk-assessments-heading">
          <h2 id="risk-assessments-heading" className="text-sm font-semibold text-slate-900">
            Risk assessments
          </h2>
          <div className="mt-3 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
            <div className="flex flex-wrap items-center gap-x-10 gap-y-3">
              <div>
                <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
                  Total assessments
                </h3>
                <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">
                  {data.risk_assessments.total.toLocaleString()}
                </p>
              </div>
              <div>
                <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
                  Recovery candidates
                </h3>
                <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">
                  {data.risk_assessments.recovery_candidates.toLocaleString()}
                </p>
              </div>
              {topAnomalyTypes(data.risk_assessments.by_anomaly_type).length > 0 && (
                <div className="min-w-0">
                  <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
                    Top anomaly types
                  </h3>
                  <ul className="mt-1.5 flex flex-wrap gap-2">
                    {topAnomalyTypes(data.risk_assessments.by_anomaly_type).map(
                      ([anomaly, count]) => (
                        <li
                          key={anomaly}
                          className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700 ring-1 ring-inset ring-slate-500/20"
                        >
                          {humanizeAnomaly(anomaly)}
                          <span className="ml-1.5 tabular-nums text-slate-500">
                            {count.toLocaleString()}
                          </span>
                        </li>
                      )
                    )}
                  </ul>
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Total transactions
          </h2>
          <p className="mt-2 text-3xl font-semibold tabular-nums text-slate-900">
            {data.total.toLocaleString()}
          </p>
        </div>

        <div className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm sm:col-span-2">
          <h2 className="text-xs font-medium uppercase tracking-wide text-slate-500">States</h2>
          <ul className="mt-3 space-y-2">
            {presentStates.map((state) => (
              <StateRow key={state} state={state} count={data.by_state[state] ?? 0} total={data.total} />
            ))}
            {extraStates.map(([state, count]) => (
              <StateRow key={state} state={state} count={count} total={data.total} />
            ))}
          </ul>
        </div>
      </div>

      <section aria-labelledby="recovery-outcomes-heading">
        <h2 id="recovery-outcomes-heading" className="text-sm font-semibold text-slate-900">
          Recovery outcomes
        </h2>
        <dl className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-3">
          {DECISION_LABELS.map(([key, label]) => (
            <div
              key={key}
              className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm"
            >
              <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
                {label}
              </dt>
              <dd className="mt-2 text-2xl font-semibold tabular-nums text-slate-900">
                {data.decisions[key].toLocaleString()}
              </dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  );
}
