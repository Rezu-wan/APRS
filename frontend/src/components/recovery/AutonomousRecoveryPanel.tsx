import {
  Ban,
  CheckCircle2,
  Circle,
  CircleHelp,
  Clock,
  Loader2,
  Play,
  ShieldCheck,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { ApiError } from "../../api/client";
import { humanizeBlockedReason } from "../../api/demo";
import { useRecovery } from "../../hooks/useQueries";
import { useProcessRecovery } from "../../hooks/useMutations";
import { useAuth } from "../../context/AuthContext";
import {
  humanizeRecoveryAction,
  humanizeRecoveryDecision,
  humanizeRecoveryStatus,
  type RecoveryStatus,
} from "../../types/api";
import { formatAmount } from "../transaction/TransactionSummary";
import { ErrorState } from "../ui/ErrorState";

// ---------------------------------------------------------------------------
// Display-only mapping — no recovery logic lives in React. The backend's
// policy + safety gate own every decision; this panel visualizes the outcome.
// ---------------------------------------------------------------------------

interface StatusMeta {
  icon: LucideIcon;
  className: string;
}

const STATUS_META: Record<RecoveryStatus, StatusMeta> = {
  VERIFIED: { icon: CheckCircle2, className: "text-emerald-600" },
  COMPLETED: { icon: CheckCircle2, className: "text-emerald-600" },
  BLOCKED: { icon: Ban, className: "text-slate-600" },
  FAILED: { icon: XCircle, className: "text-red-600" },
  PENDING: { icon: Clock, className: "text-amber-600" },
  EXECUTING: { icon: Loader2, className: "text-amber-600" },
  VERIFICATION_PENDING: { icon: Clock, className: "text-blue-600" },
};

function formatDateTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function LoadingSkeleton() {
  return (
    <div aria-hidden="true" className="animate-pulse space-y-3 py-2">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="h-4 w-2/3 rounded bg-slate-200" />
      ))}
    </div>
  );
}

export function AutonomousRecoveryPanel({ transactionId }: { transactionId: string }) {
  const { user } = useAuth();
  const recoveryQuery = useRecovery(transactionId);
  const canRun = user?.role === "SYSTEM" || user?.role === "ADMIN";
  const processRecovery = useProcessRecovery(transactionId);

  return (
    <section
      aria-labelledby="autonomous-recovery-heading"
      className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <h2 id="autonomous-recovery-heading" className="text-sm font-semibold text-slate-900">
          Autonomous recovery
        </h2>
        {/* Sandbox honesty chip — always visible (spec: make the sandbox nature
            highly visible; no real money ever moves). */}
        <span className="inline-flex items-center self-start rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-600/20">
          Simulated sandbox — no real money moves
        </span>
      </div>

      <div className="mt-4">
        {recoveryQuery.isPending ? (
          <LoadingSkeleton />
        ) : recoveryQuery.isError ? (
          <RecoveryError
            error={recoveryQuery.error}
            retry={() => void recoveryQuery.refetch()}
          />
        ) : recoveryQuery.data ? (
          <RecoveryBody record={recoveryQuery.data} />
        ) : (
          <EmptyRecovery
            transactionId={transactionId}
            canRun={canRun}
            pending={processRecovery.isPending}
            onRun={() => processRecovery.mutate()}
          />
        )}
      </div>
    </section>
  );
}

function RecoveryError({ error, retry }: { error: unknown; retry: () => void }) {
  if (error instanceof ApiError && error.status === 403) {
    return (
      <p className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-500">
        Autonomous recovery unavailable for your role.
      </p>
    );
  }
  return (
    <ErrorState
      title="Autonomous recovery unavailable"
      message={error instanceof ApiError ? error.message : "Could not load the recovery record."}
      error={error}
      onRetry={retry}
    />
  );
}

function EmptyRecovery({
  transactionId,
  canRun,
  pending,
  onRun,
}: {
  transactionId: string;
  canRun: boolean;
  pending: boolean;
  onRun: () => void;
}) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <p
        className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-500"
        data-testid={`autonomous-recovery-empty-${transactionId}`}
      >
        No autonomous recovery yet.
      </p>
      {canRun && (
        <button
          type="button"
          onClick={onRun}
          disabled={pending}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
        >
          {pending ? (
            <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
          ) : (
            <Play aria-hidden="true" className="h-4 w-4" />
          )}
          {pending ? "Processing…" : "Run autonomous recovery"}
        </button>
      )}
    </div>
  );
}

function StatusChip({ status }: { status: RecoveryStatus }) {
  const meta = STATUS_META[status] ?? { icon: CircleHelp, className: "text-slate-500" };
  const Icon = meta.icon;
  return (
    <span className={`inline-flex items-center gap-1.5 text-sm font-semibold ${meta.className}`}>
      <Icon aria-hidden="true" className="h-4 w-4" />
      {humanizeRecoveryStatus(status)}
    </span>
  );
}

function RecoveryBody({ record }: { record: import("../../types/api").RecoveryRecord }) {
  const verified = record.status === "VERIFIED";
  return (
    <div className="space-y-5">
      {/* Decision + status chips */}
      <div className="flex flex-wrap items-center gap-3">
        <span className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700 ring-1 ring-inset ring-slate-500/20">
          {humanizeRecoveryDecision(
            verified
              ? "AUTO_RECOVERED"
              : record.status === "BLOCKED"
                ? "RECOVERY_BLOCKED"
                : record.action === "MANUAL_REVIEW"
                  ? "MANUAL_REVIEW_QUEUED"
                  : "ALREADY_RECOVERED",
          )}
        </span>
        <StatusChip status={record.status} />
        <span className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700 ring-1 ring-inset ring-slate-500/20">
          {humanizeRecoveryAction(record.action)}
        </span>
      </div>

      {/* Amount + provider + reference */}
      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="rounded-md border border-slate-100 px-3 py-2">
          <dt className="text-xs uppercase tracking-wide text-slate-500">Amount</dt>
          <dd className="mt-0.5 font-mono text-sm font-medium text-slate-900 tabular-nums">
            {formatAmount(record.requested_amount, record.currency)}
          </dd>
        </div>
        <div className="rounded-md border border-slate-100 px-3 py-2">
          <dt className="text-xs uppercase tracking-wide text-slate-500">Provider</dt>
          <dd className="mt-0.5 text-sm font-medium text-slate-900">
            Simulated Sandbox
            <span className="ml-1.5 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-500">
              {record.provider ?? "not called"}
            </span>
          </dd>
        </div>
        <div className="rounded-md border border-slate-100 px-3 py-2">
          <dt className="text-xs uppercase tracking-wide text-slate-500">Reference</dt>
          <dd className="mt-0.5 font-mono text-sm font-medium text-slate-900">
            {record.provider_reference ?? "—"}
          </dd>
        </div>
      </dl>

      {/* Reason */}
      {record.decision_reason && (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Reason</h3>
          <p className="mt-1 text-sm text-slate-700">{record.decision_reason}</p>
        </div>
      )}
      {record.blocked_reason && (
        <div className="rounded-md border border-red-100 bg-red-50/50 px-3 py-3" data-testid="recovery-blocked-block">
          <h3 className="text-xs font-bold uppercase tracking-wide text-red-700">
            RECOVERY BLOCKED
          </h3>
          <dl className="mt-2 space-y-1.5 text-sm">
            <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-2">
              <dt className="w-20 shrink-0 text-xs font-medium uppercase tracking-wide text-slate-500 sm:pt-0.5">
                Reason
              </dt>
              <dd className="text-slate-800">{humanizeBlockedReason(record.blocked_reason)}</dd>
            </div>
            <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-2">
              <dt className="w-20 shrink-0 text-xs font-medium uppercase tracking-wide text-slate-500 sm:pt-0.5">
                Action
              </dt>
              <dd className="text-slate-800">{humanizeRecoveryAction(record.action)}</dd>
            </div>
            <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-2">
              <dt className="w-20 shrink-0 text-xs font-medium uppercase tracking-wide text-slate-500 sm:pt-0.5">
                Provider
              </dt>
              <dd className="font-mono text-slate-800">
                {record.provider_reference ?? "NOT CALLED"}
              </dd>
            </div>
          </dl>
        </div>
      )}
      {record.failure_reason && (
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          <span className="font-medium">Failure:</span> {record.failure_reason}
        </p>
      )}

      {/* Verification */}
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Verification
        </h3>
        {verified ? (
          <p className="mt-1.5 inline-flex items-center gap-1.5 text-sm font-medium text-emerald-700">
            <ShieldCheck aria-hidden="true" className="h-4 w-4" />
            Passed — released amount matches the sandbox ledger
          </p>
        ) : (
          <p className="mt-1.5 inline-flex items-center gap-1.5 text-sm font-medium text-slate-500">
            <Circle aria-hidden="true" className="h-4 w-4" />
            Not verified
          </p>
        )}
      </div>

      {/* §16 concern separation — the decision is deterministic; AI only explains. */}
      <p
        className="text-xs text-slate-500"
        data-testid="deterministic-decision-line"
      >
        Deterministic recovery decision — policy {record.policy_version}. No AI involvement in the
        decision.
      </p>

      {/* Footer */}
      <p className="border-t border-slate-100 pt-3 text-xs text-slate-500">
        Policy {record.policy_version} · created {formatDateTime(record.created_at)}
        {record.verified_at ? ` · verified ${formatDateTime(record.verified_at)}` : ""}
      </p>
      {/* Stage 9 auditability line — shown only when the backend provides the
          fields (older backends omit them). The idempotency key is truncated
          for display; the full key lives in the server audit log. */}
      {(record.idempotency_key || record.executor_version || record.verifier_version) && (
        <p className="font-mono text-xs text-slate-400" data-testid="recovery-audit-line">
          {record.idempotency_key && (
            <span>idempotency key {record.idempotency_key.slice(0, 12)}…</span>
          )}
          {record.idempotency_key && (record.executor_version || record.verifier_version) && " · "}
          {record.executor_version && <span>executor {record.executor_version}</span>}
          {record.executor_version && record.verifier_version && " · "}
          {record.verifier_version && <span>verifier {record.verifier_version}</span>}
        </p>
      )}
    </div>
  );
}
