import { AlertTriangle } from "lucide-react";
import type { Transaction } from "../../types/api";

/**
 * Format an amount string with the currency code, e.g. "৳ 1,250.00 BDT".
 * Falls back to the raw amount if Intl rejects the currency code.
 */
export function formatAmount(amount: string, currency: string): string {
  const numeric = Number(amount);
  if (Number.isFinite(numeric)) {
    try {
      return `${new Intl.NumberFormat("en", { style: "currency", currency }).format(numeric)} ${currency}`;
    } catch {
      // Invalid currency code — fall through to plain formatting.
      return `${new Intl.NumberFormat("en", { minimumFractionDigits: 2 }).format(numeric)} ${currency}`;
    }
  }
  return `${amount} ${currency}`;
}

function formatDateTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function DefinitionRow({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 py-2 sm:flex-row sm:items-baseline sm:gap-4 sm:py-1.5">
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500 sm:w-44 sm:shrink-0">
        {term}
      </dt>
      <dd className="min-w-0 break-words text-sm text-slate-900">{children}</dd>
    </div>
  );
}

export function TransactionSummary({ transaction }: { transaction: Transaction }) {
  return (
    <section
      aria-labelledby="transaction-summary-heading"
      className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
    >
      <h2 id="transaction-summary-heading" className="text-sm font-semibold text-slate-900">
        Transaction details
      </h2>
      <dl className="mt-3 divide-y divide-slate-100">
        <DefinitionRow term="Merchant">{transaction.merchant_id}</DefinitionRow>
        <DefinitionRow term="User">{transaction.user_id}</DefinitionRow>
        <DefinitionRow term="Timestamp">{formatDateTime(transaction.timestamp)}</DefinitionRow>
        <DefinitionRow term="Failure reason">
          {transaction.failure_reason ? (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-red-50 px-2.5 py-1 text-xs font-medium text-red-700 ring-1 ring-inset ring-red-600/20">
              <AlertTriangle aria-hidden="true" className="h-3 w-3" />
              {transaction.failure_reason}
            </span>
          ) : (
            <span className="text-slate-400">—</span>
          )}
        </DefinitionRow>
        <DefinitionRow term="Gateway latency">
          {transaction.gateway_latency_ms.toLocaleString()} ms
        </DefinitionRow>
        <DefinitionRow term="Retries">{transaction.retry_count}</DefinitionRow>
        <DefinitionRow term="Network quality">{transaction.network_quality}</DefinitionRow>
        <DefinitionRow term="Previous failures">{transaction.previous_failures}</DefinitionRow>
        <DefinitionRow term="Account age">
          {transaction.account_age_days.toLocaleString()} days
        </DefinitionRow>
      </dl>
    </section>
  );
}
