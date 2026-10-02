import { Lock, ShieldCheck } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { useReleaseLimit } from "../../hooks/useMutations";
import { ApiError } from "../../api/client";
import type { Transaction } from "../../types/api";

const DECISION_LABEL: Record<string, string> = {
  LIMIT_RELEASED: "Limit released",
  MANUAL_REVIEW: "Sent to manual review",
  RECOVERY_REJECTED: "Recovery rejected",
};

function Loader() {
  return (
    <svg
      aria-hidden="true"
      className="h-4 w-4 animate-spin"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z"
      />
    </svg>
  );
}

export function RecoveryCard({ transaction }: { transaction: Transaction }) {
  const { user } = useAuth();
  const release = useReleaseLimit(transaction.transaction_id);

  const canDecide = user?.role === "SYSTEM" || user?.role === "ADMIN";
  const isPending = transaction.current_state === "RECOVERY_PENDING";
  const decision = release.data;
  const error = release.error;

  return (
    <section
      aria-labelledby="recovery-heading"
      className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
    >
      <h2 id="recovery-heading" className="flex items-center gap-2 text-sm font-semibold text-slate-900">
        <ShieldCheck aria-hidden="true" className="h-4 w-4 text-indigo-600" />
        Recovery decision
      </h2>

      <p className="mt-3 text-sm text-slate-600">
        Current state: <span className="font-medium text-slate-900">{transaction.current_state}</span>
        {transaction.safe_to_release !== null && (
          <>
            {" · "}Safe to release:{" "}
            <span className="font-medium text-slate-900">
              {transaction.safe_to_release ? "yes" : "no"}
            </span>
          </>
        )}
      </p>

      {/* Decision from a release-limit call made in this session. */}
      {decision && (
        <div
          role="status"
          className="mt-4 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm"
        >
          <p className="font-medium text-slate-900">
            Decision: {DECISION_LABEL[decision.decision] ?? decision.decision}
          </p>
          <p className="mt-1 text-slate-600">{decision.reason}</p>
          {decision.already_applied && (
            <p className="mt-1 text-xs text-slate-500">
              This decision had already been applied to the transaction.
            </p>
          )}
          <p className="mt-1 text-xs text-slate-500">
            Decided by {decision.decided_by} · {new Date(decision.decided_at).toLocaleString()}
          </p>
        </div>
      )}

      {error instanceof ApiError && (
        <div
          role="alert"
          className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800"
        >
          {error.message}
        </div>
      )}

      {canDecide && isPending && (
        <div className="mt-4">
          <button
            type="button"
            disabled={release.isPending}
            onClick={() => release.mutate()}
            className="inline-flex items-center gap-2 rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
          >
            {release.isPending ? (
              <>
                <Loader />
                Deciding…
              </>
            ) : (
              "Release limit"
            )}
          </button>
          <p className="mt-2 text-xs text-slate-500">
            The backend decides based on the model assessment — the result may be release,
            manual review, or rejection.
          </p>
        </div>
      )}

      {!canDecide && isPending && user?.role === "SUPPORT" && (
        <p className="mt-4 inline-flex items-center gap-1.5 text-xs text-slate-500">
          <Lock aria-hidden="true" className="h-3.5 w-3.5" />
          Release decisions require the SYSTEM or ADMIN role.
        </p>
      )}
    </section>
  );
}
