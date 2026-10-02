import {
  AlertOctagon,
  AlertTriangle,
  CheckCircle2,
  Circle,
  CircleHelp,
  Loader2,
  Play,
  Siren,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { ApiError } from "../../api/client";
import { useRiskAssessment } from "../../hooks/useQueries";
import { useRunAssessment } from "../../hooks/useMutations";
import { useAuth } from "../../context/AuthContext";
import { humanizeAnomaly, type RiskAssessment, type RiskLevel } from "../../types/api";
import { ErrorState } from "../ui/ErrorState";

// ---------------------------------------------------------------------------
// Display-only mapping tables — no risk logic lives in React.
// ---------------------------------------------------------------------------

interface RiskMeta {
  label: string;
  icon: LucideIcon;
  className: string;
}

const RISK_LEVEL_META: Record<RiskLevel, RiskMeta> = {
  LOW: { label: "Low", icon: CheckCircle2, className: "text-emerald-600" },
  MEDIUM: { label: "Medium", icon: AlertTriangle, className: "text-amber-600" },
  HIGH: { label: "High", icon: AlertOctagon, className: "text-red-600" },
  CRITICAL: { label: "Critical", icon: Siren, className: "text-red-600" },
  UNKNOWN: { label: "Unknown", icon: CircleHelp, className: "text-slate-500" },
};

const SEVERITY_META: Record<string, RiskMeta> = {
  CRITICAL: { label: "Critical", icon: XCircle, className: "text-red-600" },
  HIGH: { label: "High", icon: XCircle, className: "text-red-600" },
  MEDIUM: { label: "Medium", icon: AlertTriangle, className: "text-amber-600" },
  LOW: { label: "Low", icon: Circle, className: "text-slate-500" },
};

function formatScore(value: number): string {
  return value.toFixed(2);
}

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

function AnomalyChip({ assessment }: { assessment: RiskAssessment }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700 ring-1 ring-inset ring-slate-500/20">
      <CircleHelp aria-hidden="true" className="h-3.5 w-3.5" />
      {humanizeAnomaly(assessment.anomaly_type)}
    </span>
  );
}

function RiskLevelChip({ level }: { level: RiskLevel }) {
  const meta = RISK_LEVEL_META[level] ?? RISK_LEVEL_META.UNKNOWN;
  const Icon = meta.icon;
  return (
    <span className={`inline-flex items-center gap-1.5 text-sm font-semibold ${meta.className}`}>
      <Icon aria-hidden="true" className="h-4 w-4" />
      {meta.label}
    </span>
  );
}

function ScoreCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-100 px-3 py-2">
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-0.5 font-mono text-sm font-medium text-slate-900 tabular-nums">{value}</dd>
    </div>
  );
}

export function RiskAssessmentPanel({ transactionId }: { transactionId: string }) {
  const { user } = useAuth();
  const assessmentQuery = useRiskAssessment(transactionId);
  const canRun = user?.role === "SYSTEM" || user?.role === "ADMIN";
  const runAssessment = useRunAssessment(transactionId);

  return (
    <section
      aria-labelledby="risk-assessment-heading"
      className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <h2 id="risk-assessment-heading" className="text-sm font-semibold text-slate-900">
          Risk assessment
        </h2>
        <span className="inline-flex items-center self-start rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600 ring-1 ring-inset ring-slate-500/20">
          Rules + ML on simulated evidence (sandbox)
        </span>
      </div>

      <div className="mt-4">
        {assessmentQuery.isPending ? (
          <LoadingSkeleton />
        ) : assessmentQuery.isError ? (
          <AssessmentError
            error={assessmentQuery.error}
            retry={() => void assessmentQuery.refetch()}
          />
        ) : assessmentQuery.data ? (
          <AssessmentBody assessment={assessmentQuery.data.assessment} />
        ) : (
          <EmptyAssessment
            transactionId={transactionId}
            canRun={canRun}
            pending={runAssessment.isPending}
            onRun={() => runAssessment.mutate(false)}
          />
        )}
      </div>
    </section>
  );
}

function AssessmentError({ error, retry }: { error: unknown; retry: () => void }) {
  if (error instanceof ApiError && error.status === 403) {
    return (
      <p className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-500">
        Risk assessment unavailable for your role.
      </p>
    );
  }
  return (
    <ErrorState
      title="Risk assessment unavailable"
      message={error instanceof ApiError ? error.message : "Could not load the risk assessment."}
      onRetry={retry}
    />
  );
}

function EmptyAssessment({
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
        data-testid={`risk-assessment-empty-${transactionId}`}
      >
        No risk assessment yet.
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
          {pending ? "Assessing…" : "Run assessment"}
        </button>
      )}
    </div>
  );
}

function AssessmentBody({ assessment }: { assessment: RiskAssessment }) {
  return (
    <div className="space-y-5">
      {/* Anomaly + risk level chips */}
      <div className="flex flex-wrap items-center gap-3">
        <AnomalyChip assessment={assessment} />
        <RiskLevelChip level={assessment.risk_level} />
      </div>

      {/* Scores row */}
      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <ScoreCell label="Risk score" value={formatScore(assessment.risk_score)} />
        <ScoreCell
          label="ML signal"
          value={
            assessment.ml_anomaly_score !== null
              ? formatScore(assessment.ml_anomaly_score)
              : "unavailable"
          }
        />
        <ScoreCell
          label="Deterministic"
          value={formatScore(assessment.deterministic_risk_score)}
        />
      </dl>

      {/* Recovery eligibility */}
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Recovery eligibility
        </h3>
        {assessment.recovery_candidate ? (
          <p className="mt-1.5 inline-flex items-center gap-1.5 text-sm font-medium text-emerald-700">
            <CheckCircle2 aria-hidden="true" className="h-4 w-4" />
            Eligible — pending recovery decision (not automated)
          </p>
        ) : (
          <div className="mt-1.5">
            <p className="inline-flex items-center gap-1.5 text-sm font-medium text-slate-600">
              <Circle aria-hidden="true" className="h-4 w-4" />
              Not eligible
            </p>
            {assessment.recovery_block_reason && (
              <p className="mt-1 text-xs text-slate-500">
                Blocked because: {assessment.recovery_block_reason}
              </p>
            )}
          </div>
        )}
      </div>

      {/* Evidence list */}
      {assessment.evidence.length > 0 && (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Evidence</h3>
          <ul className="mt-2 space-y-1.5">
            {assessment.evidence.map((item, index) => {
              const meta = SEVERITY_META[item.severity] ?? {
                label: item.severity,
                icon: Circle,
                className: "text-slate-500",
              };
              const Icon = meta.icon;
              return (
                <li key={`${item.code}-${index}`} className="flex items-start gap-2 text-sm text-slate-700">
                  <span className={`mt-0.5 inline-flex shrink-0 items-center gap-1 text-xs font-medium ${meta.className}`}>
                    <Icon aria-hidden="true" className="h-3.5 w-3.5" />
                    {meta.label}
                  </span>
                  <span className="min-w-0">
                    {item.description}
                    <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-500">
                      {item.source}
                    </span>
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {/* Triggered rules */}
      {assessment.triggered_rules.length > 0 && (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Triggered rules
          </h3>
          <ul className="mt-2 flex flex-wrap gap-2">
            {assessment.triggered_rules.map((rule) => (
              <li
                key={rule.rule_id}
                className="inline-flex items-center rounded-full border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-700"
              >
                <span className="font-mono text-slate-500">{rule.rule_id}</span>
                <span aria-hidden="true" className="mx-1.5 text-slate-300">·</span>
                {rule.name}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Root cause from the linked reconstruction */}
      {assessment.reconstruction_root_cause && (
        <div className="rounded-md bg-slate-50 p-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Root cause</h3>
          <p className="mt-1 text-sm font-semibold text-slate-900">
            {humanizeRootCause(assessment.reconstruction_root_cause)}
          </p>
        </div>
      )}

      {/* Footer: versions + timestamp */}
      <p className="border-t border-slate-100 pt-3 text-xs text-slate-500">
        Assessed {formatDateTime(assessment.created_at)} · model {assessment.model_version} · rules{" "}
        {assessment.rule_version}
      </p>
    </div>
  );
}

const ROOT_CAUSE_LABELS: Record<string, string> = {
  NONE: "None — completed successfully",
  INCOMPLETE: "Incomplete evidence — outcome unknown",
  CUSTOMER_DEBIT_FAILED: "Customer debit failed",
  GATEWAY_TIMEOUT: "Gateway timeout",
  GATEWAY_ERROR: "Gateway error",
  MERCHANT_CONFIRMATION_TIMEOUT: "Merchant confirmation timeout",
  MERCHANT_ERROR: "Merchant error",
  SETTLEMENT_FAILED: "Settlement failed",
  SETTLEMENT_NOT_CONFIRMED: "Settlement not confirmed",
};

function humanizeRootCause(rootCause: string): string {
  return ROOT_CAUSE_LABELS[rootCause] ?? rootCause;
}
