// Stage 11B — temporal twin panel: "what did the system know at moment T?"
// Read-only; the picked timestamp is exact and later events are excluded by
// the backend (the panel only renders what it is told — no client logic).
import { useState } from "react";
import { CircleDashed, Info } from "lucide-react";
import { ApiError } from "../../api/client";
import { useTemporalState } from "../../hooks/useStage11";
import type { TemporalStateReport } from "../../api/stage11";
import { ErrorState } from "../ui/ErrorState";
import { StatusBadge } from "../transaction/StatusBadge";

/** Checklist visual language mirrors ReconstructionPanel's stage list. */
const STAGE_LABELS: Record<string, string> = {
  bank_debit: "Bank debit",
  gateway: "Gateway",
  merchant_confirmation: "Merchant confirmation",
  settlement: "Settlement",
};

const STAGE_ORDER = ["bank_debit", "gateway", "merchant_confirmation", "settlement"] as const;

const STATUS_STYLES: Record<string, string> = {
  CONFIRMED: "text-emerald-600",
  OBSERVED: "text-sky-600",
  TIMEOUT: "text-amber-600",
  ERROR: "text-red-600",
  FAILED: "text-red-600",
  NOT_CONFIRMED: "text-slate-500",
  NOT_OBSERVED: "text-slate-400",
};

function StageStatus({ status }: { status: string }) {
  const className = STATUS_STYLES[status] ?? "text-slate-400";
  return (
    <span className={`inline-flex items-center gap-1.5 text-sm font-medium ${className}`}>
      <CircleDashed aria-hidden="true" className="h-4 w-4" />
      {status}
    </span>
  );
}

function formatAsOf(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function formatConfidence(confidence: number): string {
  return new Intl.NumberFormat("en", {
    style: "percent",
    maximumFractionDigits: 0,
  }).format(confidence);
}

/** Local datetime -> <input type="datetime-local"> value string. */
function toLocalInputValue(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

function TemporalBody({ report }: { report: TemporalStateReport }) {
  const stages = report.reconstruction.stages;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <StatusBadge state={report.state_then} />
        <span className="text-xs text-slate-500">
          As of <span className="tabular-nums">{formatAsOf(report.as_of)}</span>
        </span>
        {report.last_twin_event_type && (
          <span className="font-mono text-xs text-slate-500">
            last event: {report.last_twin_event_type}
          </span>
        )}
      </div>

      <p className="text-sm text-slate-600 tabular-nums">
        {report.observed_event_count} event{report.observed_event_count === 1 ? "" : "s"} observed
        {report.excluded_event_count > 0 && (
          <> · {report.excluded_event_count} later event{report.excluded_event_count === 1 ? "" : "s"} EXCLUDED from this view</>
        )}
      </p>

      {/* Reconstruction-at-T checklist (same visual language as ReconstructionPanel) */}
      <ul className="space-y-2">
        {STAGE_ORDER.map((key) => (
          <li
            key={key}
            className="flex items-center justify-between gap-3 rounded-md border border-slate-100 px-3 py-2"
          >
            <span className="text-sm font-medium text-slate-800">{STAGE_LABELS[key]}</span>
            <StageStatus status={stages[key]} />
          </li>
        ))}
      </ul>

      <div className="rounded-md bg-slate-50 p-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Root cause at this moment</h3>
        <p className="mt-1 text-sm font-semibold text-slate-900">{report.reconstruction.root_cause}</p>
        <p className="mt-1 text-sm text-slate-600 tabular-nums">
          Confidence: {formatConfidence(report.reconstruction.reconstruction_confidence)}
        </p>
        {report.reconstruction.missing_events.length > 0 && (
          <p className="mt-2 text-xs text-slate-500">
            Missing events: {report.reconstruction.missing_events.length} ({report.reconstruction.missing_events.join(", ")})
          </p>
        )}
      </div>

      {report.uncertainty_note && (
        <div className="flex items-start gap-2 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800 ring-1 ring-inset ring-amber-600/20">
          <Info aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{report.uncertainty_note}</span>
        </div>
      )}
    </div>
  );
}

export function TemporalPanel({ transactionId }: { transactionId: string }) {
  const [mode, setMode] = useState<"datetime" | "date">("datetime");
  const [inputValue, setInputValue] = useState("");
  const [dateValue, setDateValue] = useState("");
  const [queriedAt, setQueriedAt] = useState<Date | null>(null);
  const stateQuery = useTemporalState(transactionId, queriedAt);

  function inspect() {
    if (mode === "date") {
      // Whole-day view: the state as of the END of the selected local day.
      if (!dateValue) return;
      const [y, m, d] = dateValue.split("-").map(Number);
      const endOfDay = new Date(y, m - 1, d, 23, 59, 59, 999);
      if (!Number.isNaN(endOfDay.getTime())) setQueriedAt(endOfDay);
      return;
    }
    const parsed = new Date(inputValue);
    if (!Number.isNaN(parsed.getTime())) {
      setQueriedAt(parsed);
    }
  }

  const canInspect =
    stateQuery.isFetching === false &&
    (mode === "date" ? dateValue.length > 0 : inputValue.length > 0);

  return (
    <section
      aria-labelledby="temporal-heading"
      className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
      data-testid="temporal-panel"
    >
      <h2 id="temporal-heading" className="text-sm font-semibold text-slate-900">
        State at a point in time
      </h2>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {/* Search granularity: an exact moment, or a whole day. */}
        <div
          role="group"
          aria-label="Search granularity"
          className="inline-flex overflow-hidden rounded-md border border-slate-300 shadow-sm"
        >
          <button
            type="button"
            aria-pressed={mode === "datetime"}
            onClick={() => setMode("datetime")}
            className={`px-2.5 py-1.5 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-500 ${
              mode === "datetime"
                ? "bg-indigo-600 text-white"
                : "bg-white text-slate-600 hover:bg-slate-50"
            }`}
          >
            Date &amp; time
          </button>
          <button
            type="button"
            aria-pressed={mode === "date"}
            onClick={() => setMode("date")}
            className={`px-2.5 py-1.5 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-500 ${
              mode === "date"
                ? "bg-indigo-600 text-white"
                : "bg-white text-slate-600 hover:bg-slate-50"
            }`}
          >
            Whole day
          </button>
        </div>

        {mode === "datetime" ? (
          <>
            <label htmlFor="temporal-datetime" className="sr-only">
              Past timestamp to inspect
            </label>
            <input
              id="temporal-datetime"
              type="datetime-local"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              className="rounded-md border border-slate-300 px-2.5 py-1.5 text-sm text-slate-800 shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
            />
          </>
        ) : (
          <>
            <label htmlFor="temporal-date" className="sr-only">
              Past day to inspect
            </label>
            <input
              id="temporal-date"
              data-testid="temporal-date"
              type="date"
              value={dateValue}
              onChange={(e) => setDateValue(e.target.value)}
              className="rounded-md border border-slate-300 px-2.5 py-1.5 text-sm text-slate-800 shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
            />
          </>
        )}
        <button
          type="button"
          onClick={inspect}
          disabled={!canInspect}
          className="inline-flex items-center gap-2 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
        >
          {stateQuery.isFetching && (
            <span
              aria-hidden="true"
              className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white"
            />
          )}
          Inspect state
        </button>
        <button
          type="button"
          onClick={() => {
            setMode("datetime");
            setInputValue(toLocalInputValue(new Date()));
          }}
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
        >
          Now
        </button>
      </div>
      <p className="mt-1.5 text-xs text-slate-400">
        {mode === "date"
          ? "Whole-day view — shows the state as of the END of the selected day (23:59 your local time)."
          : "Exact-moment view — later events are never included."}
      </p>

      <div className="mt-4">
        {queriedAt === null ? (
          <p className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-500">
            Pick a past moment to see exactly what the system knew then — later events are never used.
          </p>
        ) : stateQuery.isPending ? (
          <div aria-hidden="true" className="animate-pulse space-y-3 py-2">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-4 w-2/3 rounded bg-slate-200" />
            ))}
          </div>
        ) : stateQuery.isError ? (
          stateQuery.error instanceof ApiError && stateQuery.error.status === 403 ? (
            <p className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-500">
              Temporal queries are available to staff roles.
            </p>
          ) : stateQuery.error instanceof ApiError && stateQuery.error.status === 404 ? (
            <p className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-500">
              Unknown transaction.
            </p>
          ) : (
            <ErrorState
              title="Temporal query unavailable"
              message={
                stateQuery.error instanceof ApiError
                  ? stateQuery.error.message
                  : "Could not load the historical state."
              }
              onRetry={() => void stateQuery.refetch()}
            />
          )
        ) : stateQuery.data ? (
          <TemporalBody report={stateQuery.data} />
        ) : null}
      </div>

      {/* Honest causality note (spec §22) — derived from the event model, no invented chains. */}
      <p className="mt-4 border-t border-slate-100 pt-3 text-xs text-slate-400">
        Each event carries correlation_id = transaction id; causation_id is recorded when one event
        triggers another (provider events are observed facts, not caused by the engine).
      </p>
    </section>
  );
}
