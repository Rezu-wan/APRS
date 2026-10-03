import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, Search } from "lucide-react";
import { ApiError } from "../../api/client";
import { humanizeBlockedReason } from "../../api/demo";
import {
  formatMoney,
  formatTimestamp,
  humanizeCaseStatus,
  humanizeTxState,
  type QueueCounts,
} from "../../api/support";
import { useSupportCustomerSearch, useSupportOverview } from "../../hooks/useSupport";
import { useSupportStream } from "../../hooks/useSupportStream";
import { ErrorState } from "../../components/ui/ErrorState";
import { EmptyState } from "../../components/ui/EmptyState";
import { LiveIndicator } from "../../components/support/LiveIndicator";

const QUEUE_ORDER: Array<[keyof QueueCounts, string]> = [
  ["open", "OPEN"],
  ["in_progress", "IN_PROGRESS"],
  ["waiting_for_customer", "WAITING_FOR_CUSTOMER"],
  ["escalated", "ESCALATED"],
  ["resolved", "RESOLVED"],
  ["closed", "CLOSED"],
];

function QueueRow({ count, label }: { count: number; label: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</h3>
      <p className="judge-enlarge mt-1 text-xl font-semibold tabular-nums text-slate-900">
        {count.toLocaleString()}
      </p>
    </div>
  );
}

function NeedsAttentionList() {
  const overview = useSupportOverview();

  if (overview.isPending) {
    return (
      <div aria-hidden="true" className="animate-pulse space-y-2 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-4 w-3/4 rounded bg-slate-200" />
        ))}
      </div>
    );
  }
  if (overview.isError) {
    return (
      <ErrorState
        title="Needs-attention list unavailable"
        message={
          overview.error instanceof ApiError
            ? overview.error.message
            : "Could not load the needs-attention list."
        }
        onRetry={() => void overview.refetch()}
      />
    );
  }

  const items = overview.data.needs_attention;
  if (items.length === 0) {
    return (
      <div className="rounded-lg border border-slate-200 bg-white px-4 shadow-sm">
        <EmptyState
          title="No recoveries need attention"
          message="Recoveries that end blocked or in manual review will appear here."
        />
      </div>
    );
  }

  return (
    <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white shadow-sm">
      {items.map((item) => (
        <li key={item.transaction_id} className="flex flex-wrap items-center gap-x-4 gap-y-2 p-4">
          <div className="min-w-0 flex-1">
            <Link
              to={`/support/transactions/${encodeURIComponent(item.transaction_id)}`}
              className="break-all font-mono text-sm font-medium text-indigo-600 hover:text-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
            >
              {item.transaction_id}
            </Link>
            <p className="mt-0.5 text-sm text-slate-600">
              <Link
                to={`/support/customers/${encodeURIComponent(item.customer_id)}`}
                className="font-medium text-slate-700 underline-offset-2 hover:underline"
              >
                {item.customer_id}
              </Link>
              {" · "}
              {formatMoney(item.amount, item.currency)}
              {item.failure_reason ? ` · ${item.failure_reason}` : null}
            </p>
            <p className="mt-1 flex flex-wrap gap-2 text-xs">
              {item.recovery_status && (
                <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 font-medium text-slate-700 ring-1 ring-inset ring-slate-500/20">
                  {item.recovery_status}
                </span>
              )}
              {item.blocked_reason && (
                <span
                  data-testid="blocked-reason"
                  title={item.blocked_reason}
                  className="inline-flex items-center rounded-full bg-amber-50 px-2 py-0.5 font-medium text-amber-700 ring-1 ring-inset ring-amber-600/20"
                >
                  {humanizeBlockedReason(item.blocked_reason)}
                </span>
              )}
              {item.risk_level && (
                <span className="inline-flex items-center rounded-full bg-violet-50 px-2 py-0.5 font-medium text-violet-700 ring-1 ring-inset ring-violet-600/20">
                  Risk: {item.risk_level}
                </span>
              )}
            </p>
          </div>
          {item.open_case_id && (
            <span className="inline-flex items-center rounded-full bg-indigo-50 px-2.5 py-1 text-xs font-medium text-indigo-700 ring-1 ring-inset ring-indigo-600/20">
              Case open
            </span>
          )}
          <ArrowRight aria-hidden="true" className="h-4 w-4 shrink-0 text-slate-400" />
        </li>
      ))}
    </ul>
  );
}

function RecentActivityList() {
  const overview = useSupportOverview();

  if (overview.isPending) {
    return (
      <div aria-hidden="true" className="animate-pulse space-y-2 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-4 w-2/3 rounded bg-slate-200" />
        ))}
      </div>
    );
  }
  if (overview.isError) {
    // Same source query as the queue — avoid double error alerting.
    return null;
  }

  const items = overview.data.recent_activity;
  if (items.length === 0) {
    return (
      <div className="rounded-lg border border-slate-200 bg-white px-4 shadow-sm">
        <EmptyState
          title="No recent activity"
          message="Once transactions are ingested, the latest ones appear here."
        />
      </div>
    );
  }

  return (
    <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white shadow-sm">
      {items.map((item) => (
        <li key={item.transaction_id} className="flex flex-wrap items-center gap-x-4 gap-y-1 p-4">
          <div className="min-w-0 flex-1">
            <Link
              to={`/support/transactions/${encodeURIComponent(item.transaction_id)}`}
              className="break-all font-mono text-sm font-medium text-indigo-600 hover:text-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
            >
              {item.transaction_id}
            </Link>
            <p className="mt-0.5 truncate text-sm text-slate-600">
              {item.customer_name ?? item.customer_id} · {formatMoney(item.amount, item.currency)}
            </p>
          </div>
          <div className="text-right text-xs text-slate-500">
            <p>{humanizeTxState(item.current_state)}</p>
            <p>{formatTimestamp(item.timestamp)}</p>
          </div>
        </li>
      ))}
    </ul>
  );
}

function CustomerSearchPanel() {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const navigate = useNavigate();
  const search = useSupportCustomerSearch(submitted);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitted(query.trim());
  }

  return (
    <section aria-labelledby="customer-search-heading" className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6">
      <h2 id="customer-search-heading" className="text-sm font-semibold text-slate-900">
        Find a customer
      </h2>
      <p className="mt-1 text-xs text-slate-500">
        Search by customer ID, name, email, phone — or paste a transaction ID to resolve its customer.
      </p>
      <form onSubmit={handleSubmit} role="search" className="mt-3 flex gap-2">
        <label htmlFor="support-customer-search" className="sr-only">
          Search customers
        </label>
        <div className="relative flex-1">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
          />
          <input
            id="support-customer-search"
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Customer ID, name, email, phone or transaction ID…"
            maxLength={120}
            className="w-full rounded-md border border-slate-300 bg-white py-2 pl-9 pr-3 text-sm text-slate-900 placeholder:text-slate-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          />
        </div>
        <button
          type="submit"
          disabled={query.trim().length === 0}
          className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
        >
          Search
        </button>
      </form>

      {submitted.length > 0 && (
        <div className="mt-4">
          {search.isPending && (
            <p role="status" className="py-4 text-sm text-slate-500">
              Searching…
            </p>
          )}
          {search.isError && (
            <ErrorState
              title="Search failed"
              message={
                search.error instanceof ApiError
                  ? search.error.message
                  : "Could not run the customer search."
              }
              onRetry={() => void search.refetch()}
            />
          )}
          {search.isSuccess &&
            (search.data.results.length === 0 ? (
              <EmptyState
                title="No customers found"
                message={`Nothing matches “${submitted}”. Check the ID or try a different term.`}
              />
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-slate-200 text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                      <th scope="col" className="py-2 pr-4 font-medium">Customer</th>
                      <th scope="col" className="py-2 pr-4 font-medium">Contact</th>
                      <th scope="col" className="py-2 pr-4 font-medium text-right">Txns</th>
                      <th scope="col" className="py-2 pr-4 font-medium text-right">Failed</th>
                      <th scope="col" className="py-2 pr-4 font-medium text-right">Open cases</th>
                      <th scope="col" className="py-2 font-medium"><span className="sr-only">Open profile</span></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {search.data.results.map((result) => (
                      <tr key={result.customer_id}>
                        <td className="py-2.5 pr-4">
                          <p className="font-medium text-slate-900">
                            {result.full_name}
                            {!result.dataset_known && (
                              <span
                                data-testid="not-in-registry"
                                title="This customer has no registry record — they exist only as a transaction user."
                                className="ml-2 inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600 ring-1 ring-inset ring-slate-500/20"
                              >
                                Not in registry
                              </span>
                            )}
                          </p>
                          <p className="font-mono text-xs text-slate-500">{result.customer_id}</p>
                        </td>
                        <td className="py-2.5 pr-4 text-slate-600">
                          {result.email || "—"}
                          <span className="block text-xs text-slate-400">{result.phone ?? ""}</span>
                        </td>
                        <td className="py-2.5 pr-4 text-right tabular-nums text-slate-700">{result.transaction_count}</td>
                        <td className="py-2.5 pr-4 text-right tabular-nums text-slate-700">{result.failed_transaction_count}</td>
                        <td className="py-2.5 pr-4 text-right tabular-nums text-slate-700">{result.open_case_count}</td>
                        <td className="py-2.5">
                          <button
                            type="button"
                            onClick={() => navigate(`/support/customers/${encodeURIComponent(result.customer_id)}`)}
                            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 shadow-sm hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
                          >
                            Open profile
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
        </div>
      )}
    </section>
  );
}

export default function SupportOverview() {
  const overview = useSupportOverview();
  const streamStatus = useSupportStream(true);

  if (overview.isPending) {
    return (
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6" aria-hidden="true">
        {QUEUE_ORDER.map(([key]) => (
          <div key={key} className="animate-pulse rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <div className="h-3 w-16 rounded bg-slate-200" />
            <div className="mt-3 h-7 w-10 rounded bg-slate-200" />
          </div>
        ))}
      </div>
    );
  }

  if (overview.isError) {
    return (
      <ErrorState
        title="Support queue unavailable"
        message={
          overview.error instanceof ApiError
            ? overview.error.message
            : "Could not load the support queue."
        }
        onRetry={() => void overview.refetch()}
      />
    );
  }

  const counts = overview.data.cases_by_status;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-lg font-semibold text-slate-900">Support workspace</h1>
          <p className="text-sm text-slate-500">
            Customer care over the live ledger — updated from real backend events.
          </p>
        </div>
        <LiveIndicator status={streamStatus} />
      </div>

      <section aria-labelledby="queue-heading">
        <h2 id="queue-heading" className="sr-only">
          Case queue
        </h2>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
          {QUEUE_ORDER.map(([key, label]) => (
            <QueueRow key={key} count={counts[key]} label={humanizeCaseStatus(label)} />
          ))}
        </div>
      </section>

      <CustomerSearchPanel />

      <section aria-labelledby="needs-attention-heading">
        <h2 id="needs-attention-heading" className="text-sm font-semibold text-slate-900">
          Needs attention
        </h2>
        <p className="mt-0.5 text-xs text-slate-500">
          Recoveries that ended blocked or in manual review — the human work the engine routed out.
        </p>
        <div className="mt-3">
          <NeedsAttentionList />
        </div>
      </section>

      <section aria-labelledby="recent-activity-heading">
        <h2 id="recent-activity-heading" className="text-sm font-semibold text-slate-900">
          Recent activity
        </h2>
        <div className="mt-3">
          <RecentActivityList />
        </div>
      </section>
    </div>
  );
}
