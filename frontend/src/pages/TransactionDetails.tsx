import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { ApiError } from "../api/client";
import { useTimeline, useTransaction } from "../hooks/useQueries";
import { ErrorState } from "../components/ui/ErrorState";
import { RoleGate } from "../components/ui/RoleGate";
import { StatusBadge } from "../components/transaction/StatusBadge";
import { TransactionSummary, formatAmount } from "../components/transaction/TransactionSummary";
import { RiskIndicator } from "../components/recovery/RiskIndicator";
import { RecoveryCard } from "../components/recovery/RecoveryCard";
import { RecoveryPipeline } from "../components/pipeline/RecoveryPipeline";
import { SafetyGateCard } from "../components/safety/SafetyGateCard";
import { VerificationCard } from "../components/recovery/VerificationCard";
import { SandboxLedgerCard } from "../components/sandbox/SandboxLedgerCard";
import { ReconstructionPanel } from "../components/reconstruction/ReconstructionPanel";
import { AutonomousRecoveryPanel } from "../components/recovery/AutonomousRecoveryPanel";
import { RiskAssessmentPanel } from "../components/risk/RiskAssessmentPanel";
import { Timeline } from "../components/timeline/Timeline";
import { ExplanationCard } from "../components/explanation/ExplanationCard";

function HeaderSkeleton() {
  return (
    <div aria-hidden="true" className="animate-pulse rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
      <div className="h-4 w-28 rounded bg-slate-200" />
      <div className="mt-3 h-6 w-72 max-w-full rounded bg-slate-200" />
      <div className="mt-2 h-5 w-40 rounded bg-slate-200" />
    </div>
  );
}

function failurePredictionLabel(prediction: string | null): string {
  return prediction ?? "No failure prediction recorded";
}

export default function TransactionDetails() {
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
              to="/transactions"
              className="inline-flex items-center gap-1.5 text-sm font-medium text-indigo-600 hover:text-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
            >
              <ArrowLeft aria-hidden="true" className="h-4 w-4" />
              Back to search
            </Link>
          </div>
        </div>
      );
    }
    if (error instanceof ApiError && error.status === 403) {
      return (
        <ErrorState
          title="Access denied"
          message="Your role cannot view transactions."
        />
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
      {/* Header: state, id, amount */}
      <header className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <StatusBadge state={transaction.current_state} />
            <h1 className="mt-2 break-all font-mono text-base font-medium text-slate-900 sm:text-lg">
              {transaction.transaction_id}
            </h1>
            <p className="mt-1 text-xl font-semibold text-slate-900">
              {formatAmount(transaction.amount, transaction.currency)}
            </p>
          </div>
          <Link
            to="/transactions"
            className="inline-flex shrink-0 items-center gap-1.5 self-start rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
          >
            <ArrowLeft aria-hidden="true" className="h-4 w-4" />
            Back
          </Link>
        </div>
      </header>

      {/* Stage 10 — pipeline visualization directly under the header */}
      <RecoveryPipeline transactionId={transaction.transaction_id} />

      <TransactionSummary transaction={transaction} />

      <RoleGate allowed={["SYSTEM", "ADMIN", "SUPPORT"]}>
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {/* Model assessment */}
          <section
            aria-labelledby="model-assessment-heading"
            className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
          >
            <h2 id="model-assessment-heading" className="text-sm font-semibold text-slate-900">
              Model assessment
            </h2>
            <p className="mt-3 text-sm text-slate-600">
              Failure prediction:{" "}
              <span className="font-medium text-slate-900">
                {failurePredictionLabel(transaction.failure_prediction)}
              </span>
            </p>
            <div className="mt-4">
              <RiskIndicator
                riskScore={transaction.risk_score}
                safeProbability={transaction.safe_to_release_probability}
              />
            </div>
          </section>

          <RecoveryCard transaction={transaction} />
        </div>

        <AutonomousRecoveryPanel transactionId={transaction.transaction_id} />

        {/* Simulated sandbox ledger — backend gates /sandbox/ledger to staff */}
        <SandboxLedgerCard transactionId={transaction.transaction_id} />
      </RoleGate>

      {/* Payment flow reconstruction — all roles; CUSTOMER gets a muted 403 line */}
      <ReconstructionPanel transactionId={transaction.transaction_id} />

      {/* Timeline — full width */}
      <section aria-labelledby="timeline-heading">
        <h2 id="timeline-heading" className="text-sm font-semibold text-slate-900">
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

      {/* Safety gate — derived checklist from fresh evidence */}
      <SafetyGateCard transactionId={transaction.transaction_id} />

      {/* Recovery verification — self-fetching; muted honest state when none */}
      <VerificationCard transactionId={transaction.transaction_id} />

      {/* Hybrid risk assessment — all roles; CUSTOMER gets a muted 403 line */}
      <RiskAssessmentPanel transactionId={transaction.transaction_id} />

      {/* Explanation — all roles; the card locks CUSTOMER to Bangla + customer audience */}
      <ExplanationCard transactionId={transaction.transaction_id} />
    </div>
  );
}
