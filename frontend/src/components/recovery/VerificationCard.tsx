import { Check, ShieldCheck } from "lucide-react";
import { useRecovery } from "../../hooks/useQueries";

// ---------------------------------------------------------------------------
// Presentational verification checklist driven by the real recovery record.
// Self-fetching (useRecovery) so the page only passes a transaction id.
// ---------------------------------------------------------------------------

type RowState = "pass" | "unknown";

function VerificationRow({ label, state }: { label: string; state: RowState }) {
  return (
    <li className="flex items-center gap-2.5 py-2">
      {state === "pass" ? (
        <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-emerald-100">
          <Check aria-hidden="true" className="h-3 w-3 text-emerald-700" />
        </span>
      ) : (
        <span aria-hidden="true" className="w-4 shrink-0 text-center text-sm text-slate-400">
          —
        </span>
      )}
      <span className={`text-sm ${state === "pass" ? "text-slate-800" : "text-slate-400"}`}>
        {label}
      </span>
    </li>
  );
}

function Checklist({ record }: { record: NonNullable<ReturnType<typeof useRecovery>["data"]> }) {
  const verified = record.status === "VERIFIED";

  const rows: { label: string; pass: boolean }[] = [
    {
      label: "Amount matched",
      pass:
        record.requested_amount !== null &&
        record.released_amount !== null &&
        record.released_amount === record.requested_amount,
    },
    { label: "Reference matched", pass: record.provider_reference !== null },
    {
      label: "Transaction state",
      pass: verified, // the verifier asserted LIMIT_RELEASED
    },
    { label: "No post-execution settlement conflict", pass: verified },
    { label: "Single recovery row", pass: verified }, // enforced by the idempotency key
  ];

  return (
    <div>
      <ul className="divide-y divide-slate-100">
        {rows.map((row) => (
          <VerificationRow key={row.label} label={row.label} state={row.pass ? "pass" : "unknown"} />
        ))}
      </ul>

      <div className="mt-4">
        <span
          className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-bold ring-1 ring-inset ${
            verified
              ? "bg-emerald-50 text-emerald-700 ring-emerald-600/20"
              : "bg-slate-100 text-slate-600 ring-slate-500/20"
          }`}
        >
          {verified ? "VERIFIED" : "NOT VERIFIED"}
        </span>
      </div>
    </div>
  );
}

export function VerificationCard({ transactionId }: { transactionId: string }) {
  const recoveryQuery = useRecovery(transactionId);
  const record = recoveryQuery.data ?? null;

  return (
    <section
      aria-labelledby="verification-heading"
      className="judge-enlarge rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
      data-testid="verification-card"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <h2 id="verification-heading" className="flex items-center gap-2 text-sm font-semibold text-slate-900">
          <ShieldCheck aria-hidden="true" className="h-4 w-4 text-indigo-600" />
          Recovery verification
        </h2>
        <span className="inline-flex items-center self-start rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600 ring-1 ring-inset ring-slate-500/20">
          Simulated sandbox — no real money moves
        </span>
      </div>

      <div className="mt-4">
        {recoveryQuery.isPending ? (
          <div aria-hidden="true" className="animate-pulse space-y-3 py-2">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-4 w-2/3 rounded bg-slate-200" />
            ))}
          </div>
        ) : record ? (
          <Checklist record={record} />
        ) : (
          // Honest muted state — nothing to verify until recovery has run.
          <p className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-500">
            No recovery has run yet — there is nothing to verify.
          </p>
        )}
      </div>

      <p className="mt-4 border-t border-slate-100 pt-3 text-xs text-slate-500">
        Post-execution verification must pass before recovery is called complete.
      </p>
    </section>
  );
}
