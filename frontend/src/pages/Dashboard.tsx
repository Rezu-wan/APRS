import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowRight, RefreshCcw, Search, Users, Settings, Wrench } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import {
  queryKeys,
  useCustomerProfile,
  useStats,
  useTransactionList,
  useTransactionsSummary,
} from "../hooks/useQueries";
import { ApiError } from "../api/client";
import { ErrorState } from "../components/ui/ErrorState";
import { EmptyState } from "../components/ui/EmptyState";
import { StatusBadge } from "../components/transaction/StatusBadge";
import { formatAmount } from "../components/transaction/TransactionSummary";
import { humanizeAnomaly, humanizeSnakeWord, type TransactionState } from "../types/api";

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

/** Tiny neutral label chip (customer profile segment/archetype/status). */
function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600 ring-1 ring-inset ring-slate-500/20">
      {children}
    </span>
  );
}

const percentFormat = new Intl.NumberFormat("en", { style: "percent", maximumFractionDigits: 0 });

/** §27 — "How it works" pipeline, one honest sentence per stage. */
const HOW_IT_WORKS = [
  ["EVENTS", "Every provider event is captured as an immutable payment-event record."],
  ["DIGITAL TWIN", "The twin log replays the transaction's authoritative history."],
  ["RECONSTRUCTION", "A deterministic engine derives what actually happened, stage by stage."],
  ["RISK", "A hybrid engine combines deterministic evidence rules with an ML anomaly signal."],
  ["POLICY", "A conservative policy decides whether autonomous recovery is permitted."],
  ["SAFETY", "An independent gate re-checks FRESH evidence immediately before execution."],
  ["RECOVERY", "An idempotent executor releases limits on a simulated sandbox provider."],
  ["VERIFICATION", "Post-execution verification must pass before recovery is called complete."],
] as const;

function Hero() {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900 sm:text-2xl">
            AI Transaction Digital Twin
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-600">
            Autonomous recovery for failed or stalled payment transactions — with evidence, safety
            gates and post-execution verification.
          </p>
        </div>
        <span className="inline-flex shrink-0 items-center self-start rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-semibold tracking-wide text-amber-800 ring-1 ring-inset ring-amber-600/20">
          SIMULATED SANDBOX — NO REAL MONEY MOVES
        </span>
      </div>
    </section>
  );
}

function HowItWorks() {
  return (
    <section aria-labelledby="how-it-works-heading">
      <h2 id="how-it-works-heading" className="text-sm font-semibold text-slate-900">
        How it works
      </h2>
      <ol className="mt-3 space-y-2">
        {HOW_IT_WORKS.map(([name, description], index) => (
          <li
            key={name}
            className="flex items-start gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
          >
            <span
              aria-hidden="true"
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-indigo-50 text-xs font-semibold text-indigo-700"
            >
              {index + 1}
            </span>
            <div className="min-w-0">
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-800">
                {name}
              </span>
              <p className="text-sm text-slate-600">{description}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

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
    <form onSubmit={handleSubmit} role="search" className="judge-hide w-full max-w-md">
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
  const isCustomer = user?.role === "CUSTOMER";
  const isSupport = user?.role === "SUPPORT";

  // Split by role BEFORE any query hook runs: customers never fire the
  // staff-only /stats/summary request (it 403s for them), and staff never
  // fire the customer-scoped list/summary requests.
  if (isCustomer) {
    return <CustomerDashboard />;
  }
  if (isSupport) {
    return <SupportDashboard />;
  }
  return <AdminDashboard />;
}

/** Admin/System platform overview — full platform statistics and metrics. */
function AdminDashboard() {
  const stats = useStats();

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
      <Hero />

      {/* Headline metrics — real data only; each metric renders only when its
          source field exists in the stats payload (zero fabrication). */}
      <section aria-labelledby="headline-metrics-heading">
        <h2 id="headline-metrics-heading" className="sr-only">
          Headline metrics
        </h2>
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-5">
          <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
              Transactions
            </dt>
            <dd className="judge-enlarge mt-1 text-xl font-semibold tabular-nums text-slate-900">
              {data.total.toLocaleString()}
            </dd>
          </div>
          {data.risk_assessments && (
            <>
              <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
                  Risk assessments
                </dt>
                <dd className="judge-enlarge mt-1 text-xl font-semibold tabular-nums text-slate-900">
                  {data.risk_assessments.total.toLocaleString()}
                </dd>
              </div>
              <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
                  Recovery candidates
                </dt>
                <dd className="judge-enlarge mt-1 text-xl font-semibold tabular-nums text-slate-900">
                  {data.risk_assessments.recovery_candidates.toLocaleString()}
                </dd>
              </div>
            </>
          )}
          {data.autonomous_recovery && (
            <>
              <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
                  Verified recoveries
                </dt>
                <dd className="judge-enlarge mt-1 text-xl font-semibold tabular-nums text-emerald-700">
                  {data.autonomous_recovery.verified.toLocaleString()}
                </dd>
              </div>
              <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
                  Blocked recoveries
                </dt>
                <dd className="judge-enlarge mt-1 text-xl font-semibold tabular-nums text-slate-700">
                  {data.autonomous_recovery.blocked.toLocaleString()}
                </dd>
              </div>
            </>
          )}
        </dl>
      </section>

      <HowItWorks />

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

      {/* Additive Stage 8 block — sandbox recovery metrics, only when provided */}
      {data.autonomous_recovery && (
        <section aria-labelledby="autonomous-recovery-stats-heading">
          <h2 id="autonomous-recovery-stats-heading" className="text-sm font-semibold text-slate-900">
            Autonomous recovery (sandbox)
          </h2>
          <div className="mt-3 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
            <div className="flex flex-wrap items-center gap-x-10 gap-y-3">
              <div>
                <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">Attempts</h3>
                <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">
                  {data.autonomous_recovery.attempts.toLocaleString()}
                </p>
              </div>
              <div>
                <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">Verified</h3>
                <p className="mt-1 text-2xl font-semibold tabular-nums text-emerald-700">
                  {data.autonomous_recovery.verified.toLocaleString()}
                </p>
              </div>
              <div>
                <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">Blocked</h3>
                <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-700">
                  {data.autonomous_recovery.blocked.toLocaleString()}
                </p>
              </div>
              <div>
                <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">Failed</h3>
                <p className="mt-1 text-2xl font-semibold tabular-nums text-red-600">
                  {data.autonomous_recovery.failed.toLocaleString()}
                </p>
              </div>
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

      <section aria-labelledby="system-controls-heading">
        <h2 id="system-controls-heading" className="text-sm font-semibold text-slate-900">
          System Controls
        </h2>
        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Link
            to="/demo"
            className="group rounded-lg border border-slate-200 bg-white p-6 shadow-sm transition hover:border-indigo-300 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          >
            <div className="flex items-start justify-between">
              <Settings className="h-8 w-8 text-indigo-600" aria-hidden="true" />
              <ArrowRight className="h-5 w-5 text-slate-400 transition group-hover:text-indigo-600" aria-hidden="true" />
            </div>
            <h3 className="mt-4 text-lg font-semibold text-slate-900">Demo Mode</h3>
            <p className="mt-2 text-sm text-slate-600">
              Run demo scenarios, reset sandbox state, and control the demo environment.
            </p>
          </Link>

          <Link
            to="/stage11"
            className="group rounded-lg border border-slate-200 bg-white p-6 shadow-sm transition hover:border-indigo-300 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          >
            <div className="flex items-start justify-between">
              <Wrench className="h-8 w-8 text-indigo-600" aria-hidden="true" />
              <ArrowRight className="h-5 w-5 text-slate-400 transition group-hover:text-indigo-600" aria-hidden="true" />
            </div>
            <h3 className="mt-4 text-lg font-semibold text-slate-900">Sandbox Ledger</h3>
            <p className="mt-2 text-sm text-slate-600">
              View sandbox provider ledger state and recovery execution history.
            </p>
          </Link>

          <Link
            to="/simulator"
            className="group rounded-lg border border-slate-200 bg-white p-6 shadow-sm transition hover:border-indigo-300 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          >
            <div className="flex items-start justify-between">
              <Search className="h-8 w-8 text-indigo-600" aria-hidden="true" />
              <ArrowRight className="h-5 w-5 text-slate-400 transition group-hover:text-indigo-600" aria-hidden="true" />
            </div>
            <h3 className="mt-4 text-lg font-semibold text-slate-900">Policy Simulator</h3>
            <p className="mt-2 text-sm text-slate-600">
              Test recovery policy rules and understand decision logic.
            </p>
          </Link>
        </div>
      </section>
    </div>
  );
}

/** Support dashboard — customer service focused view with quick access to support tools. */
function SupportDashboard() {
  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-slate-900 sm:text-2xl">
              Support Dashboard
            </h1>
            <p className="mt-2 max-w-2xl text-sm text-slate-600">
              Customer support workspace — search customers, review transactions, and manage support cases.
            </p>
          </div>
          <span className="inline-flex shrink-0 items-center self-start rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-semibold tracking-wide text-amber-800 ring-1 ring-inset ring-amber-600/20">
            SUPPORT WORKSPACE
          </span>
        </div>
      </section>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Link
          to="/support"
          className="group rounded-lg border border-slate-200 bg-white p-6 shadow-sm transition hover:border-indigo-300 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
        >
          <div className="flex items-start justify-between">
            <Users className="h-8 w-8 text-indigo-600" aria-hidden="true" />
            <ArrowRight className="h-5 w-5 text-slate-400 transition group-hover:text-indigo-600" aria-hidden="true" />
          </div>
          <h3 className="mt-4 text-lg font-semibold text-slate-900">Customer Support</h3>
          <p className="mt-2 text-sm text-slate-600">
            Search customers, view profiles, and manage support cases.
          </p>
        </Link>

        <Link
          to="/transactions"
          className="group rounded-lg border border-slate-200 bg-white p-6 shadow-sm transition hover:border-indigo-300 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
        >
          <div className="flex items-start justify-between">
            <RefreshCcw className="h-8 w-8 text-indigo-600" aria-hidden="true" />
            <ArrowRight className="h-5 w-5 text-slate-400 transition group-hover:text-indigo-600" aria-hidden="true" />
          </div>
          <h3 className="mt-4 text-lg font-semibold text-slate-900">Transactions</h3>
          <p className="mt-2 text-sm text-slate-600">
            Look up and review customer transactions and recovery status.
          </p>
        </Link>

        <Link
          to="/simulator"
          className="group rounded-lg border border-slate-200 bg-white p-6 shadow-sm transition hover:border-indigo-300 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
        >
          <div className="flex items-start justify-between">
            <Search className="h-8 w-8 text-indigo-600" aria-hidden="true" />
            <ArrowRight className="h-5 w-5 text-slate-400 transition group-hover:text-indigo-600" aria-hidden="true" />
          </div>
          <h3 className="mt-4 text-lg font-semibold text-slate-900">Policy Simulator</h3>
          <p className="mt-2 text-sm text-slate-600">
            Test recovery policy rules and understand decision logic.
          </p>
        </Link>
      </div>

      <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900">Quick Transaction Lookup</h2>
        <QuickSearch />
      </section>

      <section className="rounded-lg border border-blue-100 bg-blue-50 p-6">
        <h2 className="text-sm font-semibold text-blue-900">Support Resources</h2>
        <ul className="mt-3 space-y-2 text-sm text-blue-800">
          <li>• Use the <strong>Support</strong> tab to search customers and view their transaction history</li>
          <li>• Use the <strong>Transactions</strong> tab to look up individual transactions by ID</li>
          <li>• Use the <strong>Simulator</strong> to understand how recovery policies work</li>
        </ul>
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Customer dashboard — the customer's OWN data only, from the role-scoped
// GET /transactions/summary and GET /transactions endpoints. Every number is
// real backend data; no staff-only endpoint is called and nothing is
// fabricated when data is missing (loading/empty/error states instead).
// ---------------------------------------------------------------------------

function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function StatCard({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6">
      <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</h3>
      {children}
    </div>
  );
}

function SkeletonCards({ count }: { count: number }) {
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3" aria-hidden="true">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="animate-pulse rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          <div className="h-3 w-20 rounded bg-slate-200" />
          <div className="mt-3 h-7 w-16 rounded bg-slate-200" />
        </div>
      ))}
    </div>
  );
}

function CustomerDashboard() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const summary = useTransactionsSummary();
  const recent = useTransactionList({ limit: 5, offset: 0 });
  // Profile is enrichment: null (identity without a dataset profile, e.g.
  // test keys) falls back to the raw bound id — never an error banner.
  const profileQuery = useCustomerProfile();
  const profile = profileQuery.data ?? null;

  const refreshing = summary.isFetching || recent.isFetching;
  function refresh() {
    void queryClient.invalidateQueries({ queryKey: queryKeys.transactionsSummary });
    void queryClient.invalidateQueries({ queryKey: ["transactions"] });
    void queryClient.invalidateQueries({ queryKey: queryKeys.customerProfile });
  }

  const header = (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-slate-900 sm:text-2xl">
          {profile ? `Hello, ${profile.full_name}` : "My payments"}
        </h1>
        <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-slate-600">
          <span>Track your transactions and any recovery in progress.</span>
          {profile ? (
            <>
              <span className="inline-flex items-center gap-1.5">
                <Chip>{humanizeSnakeWord(profile.segment)}</Chip>
                {profile.archetype && <Chip>{humanizeSnakeWord(profile.archetype)}</Chip>}
                {profile.status !== "active" && <Chip>{humanizeSnakeWord(profile.status)}</Chip>}
              </span>
              <span className="font-mono text-xs text-slate-400">{profile.customer_id}</span>
            </>
          ) : (
            user?.customerId && (
              <span className="ml-1 text-slate-500">
                Account <span className="font-mono">{user.customerId}</span>.
              </span>
            )
          )}
        </p>
      </div>
      <button
        type="button"
        onClick={refresh}
        disabled={refreshing}
        className="inline-flex shrink-0 items-center gap-2 self-start rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
      >
        <RefreshCcw aria-hidden="true" className={`h-4 w-4${refreshing ? " animate-spin" : ""}`} />
        Refresh
      </button>
    </div>
  );

  if (summary.isPending || recent.isPending) {
    return (
      <div className="space-y-6">
        {header}
        <SkeletonCards count={3} />
      </div>
    );
  }

  if (summary.isError) {
    return (
      <div className="space-y-6">
        {header}
        <ErrorState
          title="Statistics unavailable"
          message={
            summary.error instanceof ApiError
              ? summary.error.message
              : "Could not load your transaction statistics."
          }
          error={summary.error}
          onRetry={() => void summary.refetch()}
        />
      </div>
    );
  }

  const data = summary.data;
  const recentItems = recent.data?.items ?? [];

  if (data.total === 0) {
    return (
      <div className="space-y-6">
        {header}
        <EmptyState
          title="No transactions yet"
          message="Payments processed through the platform will appear here once they are recorded."
        >
          <Link
            to="/transactions"
            className="mt-2 inline-flex items-center gap-1.5 text-sm font-medium text-indigo-600 hover:text-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
          >
            Go to transactions <ArrowRight aria-hidden="true" className="h-4 w-4" />
          </Link>
        </EmptyState>
      </div>
    );
  }

  const presentStates = STATE_ORDER.filter((state) => (data.by_state[state] ?? 0) > 0);
  const extraStates = Object.entries(data.by_state)
    .filter(([state, count]) => count > 0 && !STATE_ORDER.includes(state as TransactionState))
    .sort((a, b) => b[1] - a[1]);

  return (
    <div className="space-y-6">
      {header}

      {/* Headline stats — the customer's own role-scoped aggregates. */}
      <section aria-labelledby="customer-stats-heading">
        <h2 id="customer-stats-heading" className="sr-only">
          Your transaction statistics
        </h2>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
          <StatCard label="Transactions">
            <p className="judge-enlarge mt-2 text-2xl font-semibold tabular-nums text-slate-900 sm:text-3xl">
              {data.total.toLocaleString()}
            </p>
          </StatCard>
          {Object.entries(data.amounts_by_currency).map(([currency, amount]) => (
            <StatCard key={currency} label={`Total amount (${currency})`}>
              <p className="judge-enlarge mt-2 text-2xl font-semibold tabular-nums text-slate-900 sm:text-3xl">
                {formatAmount(amount, currency)}
              </p>
            </StatCard>
          ))}
        </div>
      </section>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 lg:items-start">
        {/* Status breakdown — real by_state counts, states with zero count hidden. */}
        <section aria-labelledby="customer-states-heading">
          <h2 id="customer-states-heading" className="text-sm font-semibold text-slate-900">
            Status breakdown
          </h2>
          <div className="mt-3 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
            <ul className="space-y-2">
              {[...presentStates, ...extraStates.map(([state]) => state as TransactionState)].map(
                (state) => (
                  <StateRow
                    key={state}
                    state={state}
                    count={data.by_state[state] ?? 0}
                    total={data.total}
                  />
                )
              )}
            </ul>
          </div>
        </section>

        {/* Recent activity — newest five, straight from the scoped list. */}
        <section aria-labelledby="customer-recent-heading">
          <div className="flex items-baseline justify-between gap-2">
            <h2 id="customer-recent-heading" className="text-sm font-semibold text-slate-900">
              Recent activity
            </h2>
            {data.total > recentItems.length && (
              <Link
                to="/transactions"
                className="inline-flex items-center gap-1 text-sm font-medium text-indigo-600 hover:text-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
              >
                View all <ArrowRight aria-hidden="true" className="h-4 w-4" />
              </Link>
            )}
          </div>
          <div className="mt-3 rounded-lg border border-slate-200 bg-white shadow-sm">
            {recent.isError ? (
              <div className="p-2">
                <ErrorState
                  title="Recent activity unavailable"
                  message={
                    recent.error instanceof ApiError
                      ? recent.error.message
                      : "Could not load your recent transactions."
                  }
                  error={recent.error}
                  onRetry={() => void recent.refetch()}
                />
              </div>
            ) : recentItems.length === 0 ? (
              <p className="px-6 py-8 text-center text-sm text-slate-500">
                No transactions to show.
              </p>
            ) : (
              <ul className="divide-y divide-slate-100">
                {recentItems.map((tx) => (
                  <li key={tx.transaction_id}>
                    <Link
                      to={`/transactions/${encodeURIComponent(tx.transaction_id)}`}
                      className="flex items-center gap-3 px-4 py-3 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-500 sm:px-6"
                    >
                      <StatusBadge state={tx.current_state} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-mono text-xs text-slate-700 sm:text-sm">
                          {tx.transaction_id}
                        </span>
                        <span className="block truncate text-xs text-slate-500">
                          {tx.merchant_name ?? (tx.merchant_id || "No merchant")} ·{" "}
                          {formatDate(tx.timestamp)}
                        </span>
                      </span>
                      <span className="shrink-0 text-sm font-medium tabular-nums text-slate-900">
                        {formatAmount(tx.amount, tx.currency)}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
