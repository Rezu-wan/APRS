import { useState, type FormEvent } from "react";
import { FileText, Loader2 } from "lucide-react";
import { ApiError } from "../../api/client";
import { useCustomerReport } from "../../hooks/useQueries";
import { useFileCustomerReport } from "../../hooks/useMutations";
import { ErrorState } from "../ui/ErrorState";
import {
  humanizeProblemStage,
  humanizeProblemType,
  humanizeReportStatus,
  type ProblemStage,
  type ProblemType,
} from "../../types/api";

// ---------------------------------------------------------------------------
// Customer problem reports — EVIDENCE ONLY, exactly like the backend contract:
// filing a report never changes transaction state or a recovery decision.
// The card shows the customer's report when one exists, otherwise the form.
// ---------------------------------------------------------------------------

const PROBLEM_TYPES: Array<[ProblemType, string]> = [
  ["PAYMENT_FAILED", "Payment failed"],
  ["DOUBLE_CHARGED", "Charged more than once"],
  ["MONEY_NOT_RECEIVED", "Money not received"],
  ["UNAUTHORIZED", "Transaction not recognised"],
  ["OTHER", "Other"],
];

const PROBLEM_STAGES: Array<[ProblemStage, string]> = [
  ["CARD_DEBIT", "Bank debit"],
  ["GATEWAY", "Gateway"],
  ["MERCHANT_CONFIRMATION", "Merchant confirmation"],
  ["SETTLEMENT", "Settlement"],
  ["NOT_SURE", "Not sure"],
];

function formatDateTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function ReportView({
  report,
  alreadyReportedNote,
}: {
  report: NonNullable<ReturnType<typeof useCustomerReport>["data"]>;
  alreadyReportedNote?: string;
}) {
  return (
    <div>
      {alreadyReportedNote && (
        <p role="status" className="mb-3 rounded-md bg-indigo-50 px-3 py-2 text-sm text-indigo-800">
          {alreadyReportedNote}
        </p>
      )}
      <dl className="space-y-2 text-sm">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">Status</dt>
          <dd>
            <span className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-700 ring-1 ring-inset ring-slate-500/20">
              {humanizeReportStatus(report.status)}
            </span>
          </dd>
        </div>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">Problem</dt>
          <dd className="text-slate-900">{humanizeProblemType(report.problem_type)}</dd>
        </div>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">Stage</dt>
          <dd className="text-slate-900">{humanizeProblemStage(report.stage)}</dd>
        </div>
        {report.description && (
          <div>
            <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
              Description
            </dt>
            <dd className="mt-1 whitespace-pre-wrap break-words text-slate-900">
              {report.description}
            </dd>
          </div>
        )}
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">Filed</dt>
          <dd className="text-slate-600">{formatDateTime(report.created_at)}</dd>
        </div>
      </dl>
    </div>
  );
}

function ReportForm({
  transactionId,
  onFiled,
}: {
  transactionId: string;
  onFiled: (alreadyReported: boolean) => void;
}) {
  const [problemType, setProblemType] = useState<ProblemType>("PAYMENT_FAILED");
  const [stage, setStage] = useState<ProblemStage>("NOT_SURE");
  const [description, setDescription] = useState("");

  const fileReport = useFileCustomerReport(transactionId);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (fileReport.isPending) return;
    fileReport.mutate(
      { problemType, stage, description: description.trim() },
      {
        onSuccess: (data) => onFiled(data.already_reported),
      }
    );
  }

  const error =
    fileReport.error instanceof ApiError
      ? fileReport.error.message
      : fileReport.error
        ? "Could not file the report. Please try again."
        : null;

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      {error && (
        <p role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </p>
      )}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label
            htmlFor="report-problem-type"
            className="block text-xs font-medium text-slate-600"
          >
            What happened?
          </label>
          <select
            id="report-problem-type"
            value={problemType}
            onChange={(event) => setProblemType(event.target.value as ProblemType)}
            className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          >
            {PROBLEM_TYPES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="report-stage" className="block text-xs font-medium text-slate-600">
            Where did it happen?
          </label>
          <select
            id="report-stage"
            value={stage}
            onChange={(event) => setStage(event.target.value as ProblemStage)}
            className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          >
            {PROBLEM_STAGES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div>
        <label htmlFor="report-description" className="block text-xs font-medium text-slate-600">
          Description <span className="font-normal text-slate-400">(optional)</span>
        </label>
        <textarea
          id="report-description"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          maxLength={2000}
          rows={3}
          placeholder="Describe what happened in your own words…"
          className="mt-1 w-full resize-y rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
        />
        <p className="mt-1 text-xs text-slate-400">{description.length}/2000</p>
      </div>
      <button
        type="submit"
        disabled={fileReport.isPending}
        className="inline-flex items-center gap-2 rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
      >
        {fileReport.isPending && <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />}
        {fileReport.isPending ? "Filing…" : "File report"}
      </button>
    </form>
  );
}

export function CustomerReportCard({ transactionId }: { transactionId: string }) {
  const reportQuery = useCustomerReport(transactionId);
  // Success note lives here so it survives the form → report-view swap.
  const [filedNote, setFiledNote] = useState<string | null>(null);

  return (
    <section
      aria-labelledby="customer-report-heading"
      className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
    >
      <h2
        id="customer-report-heading"
        className="flex items-center gap-2 text-sm font-semibold text-slate-900"
      >
        <FileText aria-hidden="true" className="h-4 w-4 text-indigo-600" />
        Report a problem
      </h2>
      <p className="mt-1 text-xs text-slate-500">
        Evidence only — a report never changes the transaction state or an automatic decision.
      </p>

      <div className="mt-4">
        {reportQuery.isPending ? (
          <div aria-hidden="true" className="animate-pulse space-y-2">
            <div className="h-4 w-40 rounded bg-slate-200" />
            <div className="h-4 w-24 rounded bg-slate-200" />
          </div>
        ) : reportQuery.isError ? (
          <ErrorState
            title="Report unavailable"
            message={
              reportQuery.error instanceof ApiError
                ? reportQuery.error.message
                : "Could not load the report for this transaction."
            }
            error={reportQuery.error}
            onRetry={() => void reportQuery.refetch()}
          />
        ) : reportQuery.data ? (
          <ReportView report={reportQuery.data} alreadyReportedNote={filedNote ?? undefined} />
        ) : (
          <ReportForm
            transactionId={transactionId}
            onFiled={(alreadyReported) =>
              setFiledNote(
                alreadyReported
                  ? "A report was already on file for this transaction — showing it unchanged."
                  : "Report filed. It is recorded as evidence on the transaction's timeline."
              )
            }
          />
        )}
      </div>
    </section>
  );
}
