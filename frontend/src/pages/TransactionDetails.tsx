import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { ApiError } from "../api/client";
import { useAuth } from "../context/AuthContext";
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
import { CustomerReportCard } from "../components/customer/CustomerReportCard";
import { TemporalPanel } from "../components/stage11/TemporalPanel";
import { BehavioralSignalsPanel } from "../components/stage11/BehavioralSignalsPanel";
import { RelationshipsPanel } from "../components/stage11/RelationshipsPanel";
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
  const { user } = useAuth();
  const isCustomer = user?.role === "CUSTOMER";
  const transactionQuery = useTransaction(transactionId);
  // The timeline read rides on the same authorization as the transaction read
  // — hold it until that read has succeeded so an unauthorized id produces a
  // single 403 (the ownership probe) and no doomed follow-up. Backend
  // authorization stays the source of truth; this only avoids a request the
  // frontend already knows is forbidden.
  const timelineQuery = useTimeline(transactionId, { enabled: transactionQuery.isSuccess });

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
      // The backend answers 403 (not 404) for unknown AND un-owned ids so the
      // endpoint cannot be used to enumerate transactions — keep the message
      // non-enumerating and role-appropriate.
      return (
        <ErrorState
          title={isCustomer ? "Not available on your account" : "Access denied"}
          message={
            isCustomer
              ? "This transaction is not available on your account."
              : "Your role cannot view transactions."
          }
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

      {/* Stage 10 — pipeline visualization. Staff-only reads: the pipeline
          combines risk-assessment + recovery reads that 403 for CUSTOMER, so
          customers get the honest summary from the header + timeline instead.
          (Backend authorization remains the source of truth — the frontend
          merely avoids requests it already knows are forbidden.) */}
      {!isCustomer && <RecoveryPipeline transactionId={transaction.transaction_id} />}

      <TransactionSummary transaction={transaction} />

      {/* silent: for CUSTOMER this staff block is simply not part of the page
          (no AccessDenied card mid-page, no staff queries fired). */}
      <RoleGate allowed={["SYSTEM", "ADMIN", "SUPPORT"]} silent>
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {/* Model assessment */}
          <section
            aria-labelledby="model-assessment-heading"
            className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
          >
            <h2 id="model-assessment-heading" className="text-sm font-semibold text-slate-900">
              Model assessment
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              Stage-2 model snapshot (advisory) — decisions are driven by the Stage-7
              hybrid risk assessment further down this page.
            </p>
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

      {/* Payment flow reconstruction — all roles (customer-scoped server-side) */}
      <ReconstructionPanel transactionId={transaction.transaction_id} />

      {/* Customer problem reports — evidence-only filing/reading (all roles) */}
      <CustomerReportCard transactionId={transaction.transaction_id} />

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

      {/* Staff-only recovery internals — hidden for CUSTOMER (these panels
          read staff-gated endpoints; rendering them for a customer would just
          produce 403s and muted "unavailable for your role" lines). */}
      {!isCustomer && (
        <>
          {/* Safety gate — derived checklist from fresh evidence */}
          <SafetyGateCard transactionId={transaction.transaction_id} />

          {/* Recovery verification — self-fetching; muted honest state when none */}
          <VerificationCard transactionId={transaction.transaction_id} />

          {/* Hybrid risk assessment */}
          <RiskAssessmentPanel transactionId={transaction.transaction_id} />

          {/* Stage 11 intelligence — temporal twin, behavioral + relationship signals */}
          <TemporalPanel transactionId={transaction.transaction_id} />
          <BehavioralSignalsPanel transactionId={transaction.transaction_id} />
          <RelationshipsPanel transactionId={transaction.transaction_id} />
        </>
      )}

      {/* Explanation — all roles; the card locks CUSTOMER to Bangla + customer audience */}
      <ExplanationCard transactionId={transaction.transaction_id} />
    </div>
  );
}
