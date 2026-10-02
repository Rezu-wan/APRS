import { useMemo } from "react";
import { Wallet } from "lucide-react";
import { useSandboxLedger } from "../../hooks/useDemo";

// ---------------------------------------------------------------------------
// Supplementary sandbox ledger view. Never blocks the page: any failure
// degrades to a compact muted line.
// ---------------------------------------------------------------------------

function formatLedgerAmount(amount: number, currency: string): string {
  try {
    return `${new Intl.NumberFormat("en", { style: "currency", currency }).format(amount)} ${currency}`;
  } catch {
    return `${new Intl.NumberFormat("en", { minimumFractionDigits: 2 }).format(amount)} ${currency}`;
  }
}

export function SandboxLedgerCard({ transactionId }: { transactionId: string }) {
  const ledgerQuery = useSandboxLedger();
  const ledger = ledgerQuery.data ?? null;

  const held = useMemo(() => {
    if (!ledger) return 0;
    return ledger.entries.reduce((sum, entry) => sum + (entry.held_amount - entry.released_amount), 0);
  }, [ledger]);

  const thisEntry = ledger?.entries.find((entry) => entry.transaction_id === transactionId) ?? null;

  return (
    <section
      aria-labelledby="sandbox-ledger-heading"
      className="judge-enlarge rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
      data-testid="sandbox-ledger-card"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <h2 id="sandbox-ledger-heading" className="flex items-center gap-2 text-sm font-semibold text-slate-900">
          <Wallet aria-hidden="true" className="h-4 w-4 text-indigo-600" />
          Simulated sandbox ledger
        </h2>
        <span className="inline-flex items-center self-start rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-600/20">
          SIMULATED — not a bank account
        </span>
      </div>

      {ledgerQuery.isPending ? (
        <div aria-hidden="true" className="mt-4 animate-pulse space-y-3 py-2">
          {[0, 1].map((i) => (
            <div key={i} className="h-4 w-1/2 rounded bg-slate-200" />
          ))}
        </div>
      ) : ledgerQuery.isError || !ledger ? (
        // Supplementary data — a failure never blocks the page.
        <p className="mt-4 rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-500">
          Sandbox ledger unavailable.
        </p>
      ) : (
        <div className="mt-4 space-y-4">
          <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="rounded-md border border-slate-100 px-3 py-2">
              <dt className="text-xs uppercase tracking-wide text-slate-500">Available</dt>
              <dd className="mt-0.5 font-mono text-sm font-medium text-slate-900 tabular-nums">
                {formatLedgerAmount(ledger.available_limit, ledger.currency)}
              </dd>
            </div>
            <div className="rounded-md border border-slate-100 px-3 py-2">
              <dt className="text-xs uppercase tracking-wide text-slate-500">Held</dt>
              <dd className="mt-0.5 font-mono text-sm font-medium text-slate-900 tabular-nums">
                {formatLedgerAmount(held, ledger.currency)}
              </dd>
            </div>
          </dl>

          {thisEntry ? (
            <div className="rounded-md border border-slate-100 px-3 py-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                This transaction
              </h3>
              <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-slate-800">
                <span
                  className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
                    thisEntry.status === "RELEASED"
                      ? "bg-emerald-50 text-emerald-700 ring-emerald-600/20"
                      : "bg-sky-50 text-sky-700 ring-sky-600/20"
                  }`}
                >
                  {thisEntry.status ?? "HELD"}
                </span>
                <span className="font-mono tabular-nums">
                  {formatLedgerAmount(thisEntry.held_amount, thisEntry.currency)} held
                </span>
                {thisEntry.provider_reference && (
                  <span className="font-mono text-xs text-slate-500">
                    ref {thisEntry.provider_reference}
                  </span>
                )}
              </p>
            </div>
          ) : (
            <p className="text-xs text-slate-500">
              No sandbox ledger entry for this transaction.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
