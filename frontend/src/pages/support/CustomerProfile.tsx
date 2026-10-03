import { Link, useParams } from "react-router-dom";
import { ArrowLeft, ShieldCheck } from "lucide-react";
import { ApiError } from "../../api/client";
import { formatMoney, formatTimestamp, humanizeCaseStatus } from "../../api/support";
import { useSupportCustomerProfile } from "../../hooks/useSupport";
import { ErrorState } from "../../components/ui/ErrorState";
import { LoadingState } from "../../components/ui/LoadingState";
import { StatusBadge } from "../../components/transaction/StatusBadge";
import { EmptyState } from "../../components/ui/EmptyState";

function IdentityRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="min-w-0 truncate text-sm text-slate-900" title={value}>
        {value || "—"}
      </dd>
    </div>
  );
}

function AggregateChip({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</h3>
      <p className="mt-1 text-lg font-semibold tabular-nums text-slate-900">{value}</p>
    </div>
  );
}

export default function CustomerProfile() {
  const { customerId = "" } = useParams<{ customerId: string }>();
  const profile = useSupportCustomerProfile(customerId);

  if (profile.isPending) {
    return <LoadingState label="Loading customer…" />;
  }

  if (profile.isError) {
    const error = profile.error;
    if (error instanceof ApiError && error.status === 404) {
      return (
        <div className="space-y-4">
          <ErrorState
            title="No customer found"
            message={`No customer with ID "${customerId}" exists in the registry or the ledger.`}
          />
          <div className="text-center">
            <Link
              to="/support"
              className="inline-flex items-center gap-1.5 text-sm font-medium text-indigo-600 hover:text-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
            >
              <ArrowLeft aria-hidden="true" className="h-4 w-4" />
              Back to support
            </Link>
          </div>
        </div>
      );
    }
    if (error instanceof ApiError && error.status === 403) {
      return <ErrorState title="Access denied" message="Your role cannot view customer profiles." />;
    }
    return (
      <ErrorState
        message={error instanceof ApiError ? error.message : "Could not load the customer profile."}
        onRetry={() => void profile.refetch()}
      />
    );
  }

  const data = profile.data;
  const customer = data.customer;
  const aggregate = data.aggregate;

  return (
    <div className="space-y-6">
      <div>
        <Link
          to="/support"
          className="inline-flex items-center gap-1.5 text-sm font-medium text-indigo-600 hover:text-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
        >
          <ArrowLeft aria-hidden="true" className="h-4 w-4" />
          Back to support
        </Link>
      </div>

      {/* Identity */}
      <section
        aria-labelledby="customer-identity-heading"
        className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
      >
        <div className="flex flex-wrap items-center gap-3">
          <h1 id="customer-identity-heading" className="text-lg font-semibold text-slate-900">
            {customer.full_name}
          </h1>
          {!data.dataset_known && (
            <span
              data-testid="not-in-registry"
              title="This customer has no registry record — they exist only as a transaction user."
              className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-600 ring-1 ring-inset ring-slate-500/20"
            >
              Not in registry
            </span>
          )}
        </div>
        <p className="mt-0.5 font-mono text-sm text-slate-500">{customer.customer_id}</p>
        <dl className="mt-4 grid grid-cols-1 gap-x-8 sm:grid-cols-2">
          <IdentityRow label="Email" value={customer.email} />
          <IdentityRow label="Phone" value={customer.phone ?? ""} />
          <IdentityRow label="Status" value={customer.status} />
          <IdentityRow label="Segment" value={customer.segment ?? ""} />
          <IdentityRow label="Risk profile" value={customer.risk_profile ?? ""} />
          <IdentityRow label="Country" value={customer.country ?? ""} />
        </dl>
        <p className="mt-4 flex items-center gap-1.5 text-xs text-slate-400">
          <ShieldCheck aria-hidden="true" className="h-3.5 w-3.5" />
          Profile views are audited.
        </p>
      </section>

      {/* Aggregate activity */}
      <section aria-labelledby="customer-aggregate-heading">
        <h2 id="customer-aggregate-heading" className="sr-only">
          Activity summary
        </h2>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <AggregateChip label="Transactions" value={aggregate.transaction_count.toLocaleString()} />
          <AggregateChip label="Failed" value={aggregate.failed_count.toLocaleString()} />
          <AggregateChip label="Recovered" value={aggregate.recovered_count.toLocaleString()} />
          <AggregateChip label="Last activity" value={formatTimestamp(aggregate.last_activity_at)} />
        </div>
      </section>

      {/* Open cases */}
      <section aria-labelledby="customer-cases-heading">
        <h2 id="customer-cases-heading" className="text-sm font-semibold text-slate-900">
          Open cases
        </h2>
        <div className="mt-3">
          {data.open_cases.length === 0 ? (
            <div className="rounded-lg border border-slate-200 bg-white px-4 shadow-sm">
              <EmptyState title="No open cases" message="Open a case from any of the customer's transactions." />
            </div>
          ) : (
            <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white shadow-sm">
              {data.open_cases.map((c) => (
                <li key={c.case_id} className="flex flex-wrap items-center gap-x-4 gap-y-1 p-4">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-slate-900">{c.subject}</p>
                    <p className="mt-0.5 font-mono text-xs text-slate-500">
                      {c.case_id} · {formatTimestamp(c.created_at)}
                    </p>
                  </div>
                  <span className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700 ring-1 ring-inset ring-slate-500/20">
                    {humanizeCaseStatus(c.status)} · {c.priority}
                  </span>
                  <Link
                    to={`/support/transactions/${encodeURIComponent(c.transaction_id)}`}
                    className="text-sm font-medium text-indigo-600 hover:text-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
                  >
                    Open transaction
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      {/* Recent transactions */}
      <section aria-labelledby="customer-transactions-heading">
        <h2 id="customer-transactions-heading" className="text-sm font-semibold text-slate-900">
          Recent transactions
        </h2>
        <div className="mt-3">
          {data.recent_transactions.length === 0 ? (
            <div className="rounded-lg border border-slate-200 bg-white px-4 shadow-sm">
              <EmptyState title="No transactions" message="This customer has no transactions in the ledger yet." />
            </div>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm">
              <table className="min-w-full divide-y divide-slate-200 text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                    <th scope="col" className="px-4 py-2 font-medium">Transaction</th>
                    <th scope="col" className="px-4 py-2 font-medium">State</th>
                    <th scope="col" className="px-4 py-2 font-medium text-right">Amount</th>
                    <th scope="col" className="px-4 py-2 font-medium">When</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.recent_transactions.map((tx) => (
                    <tr key={tx.transaction_id}>
                      <td className="px-4 py-2.5">
                        <Link
                          to={`/support/transactions/${encodeURIComponent(tx.transaction_id)}`}
                          className="break-all font-mono text-indigo-600 hover:text-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
                        >
                          {tx.transaction_id}
                        </Link>
                        {tx.failure_reason && (
                          <span className="ml-2 text-xs text-red-600">{tx.failure_reason}</span>
                        )}
                      </td>
                      <td className="px-4 py-2.5">
                        <StatusBadge state={tx.current_state} />
                      </td>
                      <td className="px-4 py-2.5 text-right tabular-nums text-slate-900">
                        {formatMoney(tx.amount, tx.currency)}
                      </td>
                      <td className="px-4 py-2.5 text-xs text-slate-500">{formatTimestamp(tx.timestamp)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      {/* Accounts */}
      <section aria-labelledby="customer-accounts-heading">
        <h2 id="customer-accounts-heading" className="text-sm font-semibold text-slate-900">
          Accounts
        </h2>
        <div className="mt-3">
          {data.accounts.length === 0 ? (
            <div className="rounded-lg border border-slate-200 bg-white px-4 shadow-sm">
              <EmptyState title="No accounts" message="No accounts are linked to this customer in the registry." />
            </div>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm">
              <table className="min-w-full divide-y divide-slate-200 text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                    <th scope="col" className="px-4 py-2 font-medium">Account</th>
                    <th scope="col" className="px-4 py-2 font-medium">Type</th>
                    <th scope="col" className="px-4 py-2 font-medium text-right">Balance</th>
                    <th scope="col" className="px-4 py-2 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.accounts.map((account) => (
                    <tr key={account.account_id}>
                      <td className="px-4 py-2.5 font-mono text-slate-900">
                        {account.account_id}
                        {account.is_primary && (
                          <span className="ml-2 inline-flex items-center rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700 ring-1 ring-inset ring-indigo-600/20">
                            Primary
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-slate-600">{account.account_type}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums text-slate-900">
                        {formatMoney(account.balance, account.currency)}
                      </td>
                      <td className="px-4 py-2.5 text-slate-600">{account.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      {/* Behavior signals — internal analytics, collapsed by default */}
      {data.behavior_signals.length > 0 && (
        <details className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6">
          <summary className="cursor-pointer text-sm font-medium text-slate-700">
            Behavior signals ({data.behavior_signals.length}) — internal analytics
          </summary>
          <div className="mt-4 overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                  <th scope="col" className="py-2 pr-4 font-medium">Signal</th>
                  <th scope="col" className="py-2 pr-4 font-medium text-right">Value</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Level</th>
                  <th scope="col" className="py-2 font-medium">Window</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.behavior_signals.map((signal) => (
                  <tr key={signal.signal_name}>
                    <td className="py-2 pr-4 text-slate-900">{signal.signal_name}</td>
                    <td className="py-2 pr-4 text-right tabular-nums text-slate-700">
                      {signal.signal_value !== null ? signal.signal_value : "—"}
                      {signal.unit ? ` ${signal.unit}` : ""}
                    </td>
                    <td className="py-2 pr-4 text-slate-600">{signal.signal_level}</td>
                    <td className="py-2 text-slate-500">{signal.window ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  );
}
