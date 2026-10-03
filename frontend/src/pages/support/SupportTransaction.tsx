import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { ApiError } from "../../api/client";
import { formatMoney, humanizeTxState } from "../../api/support";
import { useTimeline, useTransaction } from "../../hooks/useQueries";
import { ErrorState } from "../../components/ui/ErrorState";
import { StatusBadge } from "../../components/transaction/StatusBadge";
import { TransactionSummary } from "../../components/transaction/TransactionSummary";
import { RecoveryPipeline } from "../../components/pipeline/RecoveryPipeline";
import { RiskIndicator } from "../../components/recovery/RiskIndicator";
import { RecoveryCard } from "../../components/recovery/RecoveryCard";
import { SafetyGateCard } from "../../components/safety/SafetyGateCard";
import { VerificationCard } from "../../components/recovery/VerificationCard";
import { ReconstructionPanel } from "../../components/reconstruction/ReconstructionPanel";
import { RiskAssessmentPanel } from "../../components/risk/RiskAssessmentPanel";
import { TemporalPanel } from "../../components/stage11/TemporalPanel";
import { Timeline } from "../../components/timeline/Timeline";
import { CasePanel } from "../../components/support/CasePanel";

function HeaderSkeleton() {
  return (
    <div aria-hidden="true" className="animate-pulse rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
      <div className="h-4 w-28 rounded bg-slate-200" />
      <div className="mt-3 h-6 w-72 max-w-full rounded bg-slate-200" />
      <div className="mt-2 h-5 w-40 rounded bg-slate-200" />
    </div>
  );
}

/**
 * Support investigation view: the same evidence the engineering view serves,
 * arranged customer-first. Engine actions stay gated inside their own panels
 * (RecoveryCard already refuses decisions to non-SYSTEM/ADMIN); SUPPORT gets
 * read access plus the case workflow.
 */
export default function SupportTransaction() {
  const { transactionId = "" } = useParams<{ transactionId: string }>();
  const transactionQuery = useTransaction(transactionId);
  const timelineQuery = useTimeline(transactionId);

  if (transactionQuery.isPending) {
    return (
      <div className="space-y-6">
        <HeaderSkeleton />
        <HeaderSkeleton />
      </div>
    );
  }

  if (transactionQuery.isError) {
    const error = transactionQuery.error;
    if (error instanceof ApiError && error.status === 404) {
      return (
        <div className="space-y-4">
          <ErrorState
            title="Transaction not found"
            message={`No transaction with ID "${transactionId}" exists.`}
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
      return (
        <ErrorState title="Access denied" message="Your role cannot view this transaction." />
      );
    }
    return (
      <ErrorState
        message={error instanceof ApiError ? error.message : "Could not load the transaction."}
        onRetry={() => void transactionQuery.refetch()}
      />
    );
  }

  const transaction = transactionQuery.data;

  return (
    <div className="space-y-6">
      {/* Customer-first context strip */}
      <header className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge state={transaction.current_state} />
              <span className="text-xs text-slate-500">{humanizeTxState(transaction.current_state)}</span>
            </div>
            <h1 className="mt-2 break-all font-mono text-base font-medium text-slate-900 sm:text-lg">
              {transaction.transaction_id}
            </h1>
            <p className="mt-1 text-sm text-slate-600">
              Customer{" "}
              <Link
                to={`/support/customers/${encodeURIComponent(transaction.user_id)}`}
                data-testid="customer-link"
                className="font-medium text-indigo-600 underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
              >
                {transaction.user_id}
              </Link>
              {" · "}
              <span className="font-semibold text-slate-900">
                {formatMoney(Number(transaction.amount), transaction.currency)}
              </span>
              {transaction.failure_reason ? ` · ${transaction.failure_reason}` : ""}
            </p>
          </div>
          <Link
            to="/support"
            className="inline-flex shrink-0 items-center gap-1.5 self-start rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
          >
            <ArrowLeft aria-hidden="true" className="h-4 w-4" />
            Back to support
          </Link>
        </div>
      </header>

      {/* Case workflow — the writable part of the support workspace */}
      <CasePanel transactionId={transaction.transaction_id} />

      {/* Where the payment stands in the recovery pipeline */}
      <RecoveryPipeline transactionId={transaction.transaction_id} />

      <TransactionSummary transaction={transaction} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Model assessment — advisory evidence for the agent; raw probabilities
            are technical detail, collapsed */}
        <section
          aria-labelledby="support-model-assessment-heading"
          className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
        >
          <h2 id="support-model-assessment-heading" className="text-sm font-semibold text-slate-900">
            Model assessment
          </h2>
          <p className="mt-3 text-sm text-slate-600">
            Failure prediction:{" "}
            <span className="font-medium text-slate-900">
              {transaction.failure_prediction ?? "No failure prediction recorded"}
            </span>
          </p>
          <details className="mt-3">
            <summary className="cursor-pointer text-xs font-medium text-slate-500">
              Technical detail — probabilities
            </summary>
            <div className="mt-2">
              <RiskIndicator
                riskScore={transaction.risk_score}
                safeProbability={transaction.safe_to_release_probability}
              />
            </div>
          </details>
        </section>

        <RecoveryCard transaction={transaction} />
      </div>

      {/* Payment flow reconstruction — what actually happened, per evidence */}
      <ReconstructionPanel transactionId={transaction.transaction_id} />

      {/* Timeline */}
      <section aria-labelledby="support-timeline-heading">
        <h2 id="support-timeline-heading" className="text-sm font-semibold text-slate-900">
          Timeline
        </h2>
        <div className="mt-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6">
          {timelineQuery.isPending ? (
            <div aria-hidden="true" className="animate-pulse space-y-4 py-2">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-4 w-3/4 rounded bg-slate-200" />
              ))}
            </div>
          ) : timelineQuery.isError ? (
            <ErrorState
              title="Timeline unavailable"
              message={
                timelineQuery.error instanceof ApiError
                  ? timelineQuery.error.message
                  : "Could not load the event timeline."
              }
              onRetry={() => void timelineQuery.refetch()}
            />
          ) : (
            <Timeline events={timelineQuery.data.events} />
          )}
        </div>
      </section>

      {/* Safety gate + verification — recovery evidence without execution */}
      <SafetyGateCard transactionId={transaction.transaction_id} />
      <VerificationCard transactionId={transaction.transaction_id} />

      {/* Hybrid risk assessment — evidence; its run action is role-gated inside */}
      <RiskAssessmentPanel transactionId={transaction.transaction_id} />

      {/* Temporal twin — internal engineering detail, collapsed */}
      <details className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6">
        <summary className="cursor-pointer text-sm font-medium text-slate-700">
          Internal detail — temporal twin
        </summary>
        <div className="mt-4">
          <TemporalPanel transactionId={transaction.transaction_id} />
        </div>
      </details>
    </div>
  );
}
