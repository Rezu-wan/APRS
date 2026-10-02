import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  Clock,
  Timer,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { ApiError } from "../../api/client";
import { useReconstruction } from "../../hooks/useQueries";
import type {
  PaymentEventOut,
  ReconstructionEventStatus,
  ReconstructionStage,
  ReconstructionResult,
} from "../../types/api";
import { ErrorState } from "../ui/ErrorState";

// ---------------------------------------------------------------------------
// Display-only mapping tables — no reconstruction logic lives in React.
// ---------------------------------------------------------------------------

const STAGES: ReconstructionStage[] = [
  "BANK_DEBIT",
  "GATEWAY",
  "MERCHANT_CONFIRMATION",
  "SETTLEMENT",
];

const STAGE_LABELS: Record<ReconstructionStage, string> = {
  BANK_DEBIT: "Bank debit",
  GATEWAY: "Gateway",
  MERCHANT_CONFIRMATION: "Merchant confirmation",
  SETTLEMENT: "Settlement",
  UNAVAILABLE: "Unavailable",
};

/** Stage -> which status field on the result feeds its checklist row. */
type StageStatusKey = "customer_debit_status" | "gateway_status" | "merchant_confirmation_status" | "settlement_status";
const STAGE_STATUS_KEY: Partial<Record<ReconstructionStage, StageStatusKey>> = {
  BANK_DEBIT: "customer_debit_status",
  GATEWAY: "gateway_status",
  MERCHANT_CONFIRMATION: "merchant_confirmation_status",
  SETTLEMENT: "settlement_status",
};

interface StatusMeta {
  label: string;
  icon: LucideIcon;
  className: string;
}

const STATUS_META: Record<ReconstructionEventStatus, StatusMeta> = {
  CONFIRMED: {
    label: "Confirmed",
    icon: CheckCircle2,
    className: "text-emerald-600",
  },
  OBSERVED: {
    label: "Observed",
    icon: Clock,
    className: "text-sky-600",
  },
  TIMEOUT: {
    label: "Timeout",
    icon: Timer,
    className: "text-amber-600",
  },
  ERROR: {
    label: "Error",
    icon: AlertTriangle,
    className: "text-red-600",
  },
  FAILED: {
    label: "Failed",
    icon: XCircle,
    className: "text-red-600",
  },
  NOT_CONFIRMED: {
    label: "Not confirmed",
    icon: CircleDashed,
    className: "text-slate-500",
  },
  NOT_OBSERVED: {
    label: "Not observed",
    icon: CircleDashed,
    className: "text-slate-400",
  },
};

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

function humanizeEventType(eventType: string): string {
  return eventType
    .toLowerCase()
    .split(/[_\s-]+/)
    .map((word) => (word.length > 0 ? word[0].toUpperCase() + word.slice(1) : word))
    .join(" ");
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleTimeString();
}

function formatConfidence(confidence: number): string {
  return new Intl.NumberFormat("en", {
    style: "percent",
    maximumFractionDigits: 0,
  }).format(confidence);
}

function StatusChip({ status }: { status: ReconstructionEventStatus }) {
  const meta = STATUS_META[status] ?? {
    label: status,
    icon: CircleDashed,
    className: "text-slate-400",
  };
  const Icon = meta.icon;
  return (
    <span className={`inline-flex items-center gap-1.5 text-sm font-medium ${meta.className}`}>
      <Icon aria-hidden="true" className="h-4 w-4" />
      {meta.label}
    </span>
  );
}

function EventRow({ event }: { event: PaymentEventOut }) {
  const meta = STATUS_META[event.status] ?? {
    label: event.status,
    icon: CircleDashed,
    className: "text-slate-400",
  };
  const Icon = meta.icon;
  return (
    <tr className="border-t border-slate-100">
      <td className="whitespace-nowrap px-2 py-1.5 text-xs text-slate-500 tabular-nums">
        {formatTime(event.event_timestamp)}
      </td>
      <td className="px-2 py-1.5 text-xs font-medium text-slate-800">
        {humanizeEventType(event.event_type)}
      </td>
      <td className="px-2 py-1.5 font-mono text-xs text-slate-500">{event.source}</td>
      <td className="whitespace-nowrap px-2 py-1.5">
        <span className={`inline-flex items-center gap-1 text-xs font-medium ${meta.className}`}>
          <Icon aria-hidden="true" className="h-3.5 w-3.5" />
          {meta.label}
        </span>
      </td>
      <td className="px-2 py-1.5 text-xs text-slate-500 tabular-nums">
        {event.latency_ms !== null ? `${event.latency_ms} ms` : "—"}
      </td>
    </tr>
  );
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

export function ReconstructionPanel({ transactionId }: { transactionId: string }) {
  const reconstructionQuery = useReconstruction(transactionId);

  return (
    <section
      aria-labelledby="reconstruction-heading"
      className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <h2 id="reconstruction-heading" className="text-sm font-semibold text-slate-900">
          Payment flow reconstruction
        </h2>
        <span className="inline-flex items-center self-start rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600 ring-1 ring-inset ring-slate-500/20">
          Reconstructed from simulated payment events (sandbox)
        </span>
      </div>

      <div className="mt-4">
        {reconstructionQuery.isPending ? (
          <LoadingSkeleton />
        ) : reconstructionQuery.isError ? (
          <ReconstructionError transactionId={transactionId} error={reconstructionQuery.error} retry={() => void reconstructionQuery.refetch()} />
        ) : reconstructionQuery.data ? (
          <ReconstructionBody result={reconstructionQuery.data} />
        ) : (
          <p className="text-sm text-slate-500">No reconstruction available.</p>
        )}
      </div>
    </section>
  );
}

function ReconstructionError({
  transactionId,
  error,
  retry,
}: {
  transactionId: string;
  error: unknown;
  retry: () => void;
}) {
  if (error instanceof ApiError && error.status === 404) {
    return (
      <p className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-500" data-testid={`reconstruction-empty-${transactionId}`}>
        No reconstruction available for this transaction.
      </p>
    );
  }
  if (error instanceof ApiError && error.status === 403) {
    return (
      <p className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-500">
        Reconstruction unavailable for your role.
      </p>
    );
  }
  return (
    <ErrorState
      title="Reconstruction unavailable"
      message={error instanceof ApiError ? error.message : "Could not load the reconstruction."}
      onRetry={retry}
    />
  );
}

function ReconstructionBody({ result }: { result: ReconstructionResult }) {
  const stageRows = STAGES.map((stage) => {
    const statusKey = STAGE_STATUS_KEY[stage];
    const status = statusKey ? result[statusKey] : null;
    return { stage, status };
  });

  // evidence_summary is stage-ordered; when it lines up with the 4 stages we
  // can prefix each line with the matching stage's status icon. Otherwise no
  // icons — do not guess.
  const alignEvidence = result.evidence_summary.length === stageRows.length;

  return (
    <div className="space-y-6">
      {/* Stage checklist */}
      <ul className="space-y-2">
        {stageRows.map(({ stage, status }) => (
          <li
            key={stage}
            className="flex items-center justify-between gap-3 rounded-md border border-slate-100 px-3 py-2"
          >
            <span className="text-sm font-medium text-slate-800">{STAGE_LABELS[stage]}</span>
            {status ? <StatusChip status={status} /> : <span className="text-sm text-slate-400">—</span>}
          </li>
        ))}
      </ul>

      {/* Root cause */}
      <div className="rounded-md bg-slate-50 p-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Root cause</h3>
        <p className="mt-1 text-sm font-semibold text-slate-900">{humanizeRootCause(result.root_cause)}</p>
        <dl className="mt-2 space-y-1 text-sm text-slate-600">
          {result.last_successful_stage !== "UNAVAILABLE" && (
            <div className="flex gap-2">
              <dt className="text-slate-500">Last successful stage:</dt>
              <dd className="font-medium text-slate-800">{STAGE_LABELS[result.last_successful_stage as ReconstructionStage] ?? result.last_successful_stage}</dd>
            </div>
          )}
          {result.failure_stage !== "UNAVAILABLE" && (
            <div className="flex gap-2">
              <dt className="text-slate-500">Failed stage:</dt>
              <dd className="font-medium text-slate-800">{STAGE_LABELS[result.failure_stage as ReconstructionStage] ?? result.failure_stage}</dd>
            </div>
          )}
          <div className="flex gap-2">
            <dt className="text-slate-500">Evidence coverage:</dt>
            <dd className="font-medium text-slate-800 tabular-nums">
              {formatConfidence(result.reconstruction_confidence)}
            </dd>
          </div>
        </dl>
      </div>

      {/* Evidence */}
      {result.evidence_summary.length > 0 && (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Evidence</h3>
          <ul className="mt-2 space-y-1.5">
            {result.evidence_summary.map((line, index) => {
              const row = alignEvidence ? stageRows[index] : null;
              const chip = row?.status ? <StatusChip status={row.status} /> : null;
              return (
                <li key={index} className="flex items-start gap-2 text-sm text-slate-700">
                  {chip ? (
                    <span className="mt-0.5 shrink-0 [&>span]:text-xs [&>span]:font-normal">{chip}</span>
                  ) : (
                    <span aria-hidden="true" className="mt-2 h-1 w-1 shrink-0 rounded-full bg-slate-400" />
                  )}
                  <span>{line}</span>
                </li>
              );
            })}
          </ul>
          {result.missing_events.length > 0 && (
            <p className="mt-2 text-xs text-slate-500">
              Missing events: {result.missing_events.join(", ")}
            </p>
          )}
        </div>
      )}

      {/* Raw events */}
      {result.ordered_events.length > 0 && (
        <details className="group rounded-md border border-slate-200">
          <summary className="cursor-pointer select-none px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500">
            Raw events ({result.ordered_events.length})
          </summary>
          <div className="max-h-80 overflow-y-auto border-t border-slate-100">
            <table className="w-full min-w-[36rem] text-left">
              <caption className="sr-only">
                Simulated provider events for this transaction, in chronological order.
              </caption>
              <thead>
                <tr className="text-xs uppercase tracking-wide text-slate-500">
                  <th scope="col" className="px-2 py-1.5 font-medium">Time</th>
                  <th scope="col" className="px-2 py-1.5 font-medium">Event</th>
                  <th scope="col" className="px-2 py-1.5 font-medium">Source</th>
                  <th scope="col" className="px-2 py-1.5 font-medium">Status</th>
                  <th scope="col" className="px-2 py-1.5 font-medium">Latency</th>
                </tr>
              </thead>
              <tbody>
                {result.ordered_events.map((event) => (
                  <EventRow key={event.event_id} event={event} />
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  );
}
