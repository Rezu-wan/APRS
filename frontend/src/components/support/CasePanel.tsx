import { useState, type FormEvent } from "react";
import { ApiError } from "../../api/client";
import {
  CASE_NEXT_STATUSES,
  formatTimestamp,
  humanizeCasePriority,
  humanizeCaseStatus,
  type CasePriority,
  type CaseStatus,
  type SupportCase,
} from "../../api/support";
import { useCreateSupportCase, useSupportCases, useUpdateSupportCase } from "../../hooks/useSupport";
import { ErrorState } from "../../components/ui/ErrorState";

const PRIORITIES: CasePriority[] = ["LOW", "MEDIUM", "HIGH", "URGENT"];

const PRIORITY_CHIP: Record<CasePriority, string> = {
  LOW: "bg-slate-100 text-slate-600 ring-slate-500/20",
  MEDIUM: "bg-sky-50 text-sky-700 ring-sky-600/20",
  HIGH: "bg-amber-50 text-amber-700 ring-amber-600/20",
  URGENT: "bg-red-50 text-red-700 ring-red-600/20",
};

const STATUS_CHIP: Record<CaseStatus, string> = {
  OPEN: "bg-indigo-50 text-indigo-700 ring-indigo-600/20",
  IN_PROGRESS: "bg-sky-50 text-sky-700 ring-sky-600/20",
  WAITING_FOR_CUSTOMER: "bg-amber-50 text-amber-700 ring-amber-600/20",
  ESCALATED: "bg-red-50 text-red-700 ring-red-600/20",
  RESOLVED: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  CLOSED: "bg-slate-100 text-slate-600 ring-slate-500/20",
};

function CaseActions({ caseItem }: { caseItem: SupportCase }) {
  const update = useUpdateSupportCase();
  const [actionError, setActionError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const nextStatuses = CASE_NEXT_STATUSES[caseItem.status] ?? [];

  function runUpdate(input: { status?: CaseStatus; priority?: CasePriority; note?: string }) {
    setActionError(null);
    update.mutate(
      { caseId: caseItem.case_id, input },
      {
        onSuccess: () => setNote(""),
        onError: (error) => {
          // Surface the backend's verdict honestly (e.g. illegal transition 400).
          setActionError(
            error instanceof ApiError ? error.message : "The update failed. Try again."
          );
        },
      }
    );
  }

  if (nextStatuses.length === 0 && caseItem.status === "CLOSED") {
    return null; // terminal — nothing left to do, stay read-only
  }

  return (
    <div className="mt-3 border-t border-slate-100 pt-3">
      {nextStatuses.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {nextStatuses.map((next) => (
            <button
              key={next}
              type="button"
              disabled={update.isPending}
              onClick={() => runUpdate({ status: next })}
              className={`rounded-md px-3 py-1.5 text-xs font-medium shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60 ${
                next === "CLOSED"
                  ? "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
                  : "bg-indigo-600 text-white hover:bg-indigo-500"
              }`}
            >
              {next === "OPEN" && caseItem.status === "RESOLVED" ? "Reopen" : `Mark ${humanizeCaseStatus(next)}`}
            </button>
          ))}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1">
          <label
            htmlFor={`case-note-${caseItem.case_id}`}
            className="block text-xs font-medium text-slate-600"
          >
            Add note
          </label>
          <textarea
            id={`case-note-${caseItem.case_id}`}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            rows={2}
            maxLength={2000}
            className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
            placeholder="What was done or found…"
          />
        </div>
        <button
          type="button"
          disabled={note.trim().length === 0 || update.isPending}
          onClick={() => runUpdate({ note: note.trim() })}
          className="rounded-md border border-slate-300 bg-white px-3 py-2 text-xs font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
        >
          Add note
        </button>
      </div>

      <div className="mt-3 flex items-center gap-2">
        <label htmlFor={`case-priority-${caseItem.case_id}`} className="text-xs font-medium text-slate-600">
          Priority
        </label>
        <select
          id={`case-priority-${caseItem.case_id}`}
          value={caseItem.priority}
          disabled={update.isPending}
          onChange={(event) => runUpdate({ priority: event.target.value as CasePriority })}
          className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
        >
          {PRIORITIES.map((p) => (
            <option key={p} value={p}>
              {humanizeCasePriority(p)}
            </option>
          ))}
        </select>
      </div>

      {actionError && (
        <p role="alert" className="mt-2 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700 ring-1 ring-inset ring-red-600/20">
          {actionError}
        </p>
      )}
    </div>
  );
}

function CaseCard({ caseItem }: { caseItem: SupportCase }) {
  return (
    <li data-testid="case-card" className="p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${STATUS_CHIP[caseItem.status] ?? STATUS_CHIP.CLOSED}`}
        >
          {humanizeCaseStatus(caseItem.status)}
        </span>
        <span
          className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${PRIORITY_CHIP[caseItem.priority] ?? PRIORITY_CHIP.LOW}`}
        >
          {humanizeCasePriority(caseItem.priority)}
        </span>
        <span className="ml-auto font-mono text-xs text-slate-400">{caseItem.case_id}</span>
      </div>
      <p className="mt-2 text-sm font-medium text-slate-900">{caseItem.subject}</p>
      {caseItem.description && <p className="mt-1 text-sm text-slate-600">{caseItem.description}</p>}
      <p className="mt-1 text-xs text-slate-500">
        Opened by {caseItem.created_by} · {formatTimestamp(caseItem.created_at)}
        {caseItem.assignee ? ` · assigned to ${caseItem.assignee}` : ""}
        {caseItem.resolved_at ? ` · resolved ${formatTimestamp(caseItem.resolved_at)}` : ""}
      </p>

      {caseItem.notes.length > 0 && (
        <ol className="mt-3 space-y-2 border-l-2 border-slate-100 pl-3">
          {caseItem.notes.map((note, index) => (
            <li key={`${note.at}-${index}`} className="text-sm">
              <p className="text-slate-800">{note.text}</p>
              <p className="text-xs text-slate-400">
                {note.by} ({note.role}) · {formatTimestamp(note.at)}
              </p>
            </li>
          ))}
        </ol>
      )}

      <CaseActions caseItem={caseItem} />
    </li>
  );
}

function NewCaseForm({ transactionId }: { transactionId: string }) {
  const create = useCreateSupportCase();
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState<CasePriority>("MEDIUM");
  const [error, setError] = useState<string | null>(null);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    create.mutate(
      {
        transaction_id: transactionId,
        subject: subject.trim(),
        description: description.trim() || null,
        priority,
      },
      {
        onSuccess: () => {
          setSubject("");
          setDescription("");
          setPriority("MEDIUM");
        },
        onError: (err) => {
          setError(err instanceof ApiError ? err.message : "Could not open the case. Try again.");
        },
      }
    );
  }

  return (
    <form onSubmit={handleSubmit} className="p-4" aria-label="Open a case">
      <p className="text-sm text-slate-600">
        No support case exists for this transaction yet. Open one to start tracking the customer conversation.
      </p>
      <div className="mt-3 space-y-3">
        <div>
          <label htmlFor="new-case-subject" className="block text-xs font-medium text-slate-600">
            Subject
          </label>
          <input
            id="new-case-subject"
            type="text"
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
            maxLength={140}
            required
            minLength={3}
            className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
            placeholder="e.g. Customer reports a failed payment"
          />
        </div>
        <div>
          <label htmlFor="new-case-description" className="block text-xs font-medium text-slate-600">
            Description <span className="font-normal text-slate-400">(optional)</span>
          </label>
          <textarea
            id="new-case-description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={3}
            maxLength={4000}
            className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
            placeholder="What the customer reported…"
          />
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="new-case-priority" className="text-xs font-medium text-slate-600">
            Priority
          </label>
          <select
            id="new-case-priority"
            value={priority}
            onChange={(event) => setPriority(event.target.value as CasePriority)}
            className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          >
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {humanizeCasePriority(p)}
              </option>
            ))}
          </select>
        </div>
      </div>
      {error && (
        <p role="alert" className="mt-3 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700 ring-1 ring-inset ring-red-600/20">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={subject.trim().length < 3 || create.isPending}
        className="mt-3 rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
      >
        Open a case
      </button>
    </form>
  );
}

/** Case workspace for one transaction — the ONLY writable state in the
 * support workspace. Transitions/notes are validated by the backend; this
 * panel renders its verdicts honestly. */
export function CasePanel({ transactionId }: { transactionId: string }) {
  const cases = useSupportCases({ transaction_id: transactionId, limit: 20 });

  if (cases.isPending) {
    return (
      <section aria-labelledby="case-panel-heading" className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6">
        <h2 id="case-panel-heading" className="text-sm font-semibold text-slate-900">
          Support cases
        </h2>
        <div aria-hidden="true" className="mt-3 animate-pulse space-y-2">
          <div className="h-4 w-2/3 rounded bg-slate-200" />
          <div className="h-4 w-1/2 rounded bg-slate-200" />
        </div>
      </section>
    );
  }

  if (cases.isError) {
    return (
      <section aria-labelledby="case-panel-heading" className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6">
        <h2 id="case-panel-heading" className="text-sm font-semibold text-slate-900">
          Support cases
        </h2>
        <ErrorState
          title="Cases unavailable"
          message={
            cases.error instanceof ApiError
              ? cases.error.message
              : "Could not load the cases for this transaction."
          }
          onRetry={() => void cases.refetch()}
        />
      </section>
    );
  }

  const openCases = cases.data.cases.filter((c) => c.status !== "CLOSED");
  const closedCases = cases.data.cases.filter((c) => c.status === "CLOSED");

  return (
    <section aria-labelledby="case-panel-heading" className="rounded-lg border border-slate-200 bg-white shadow-sm">
      <h2 id="case-panel-heading" className="border-b border-slate-100 p-4 text-sm font-semibold text-slate-900 sm:p-6 sm:pb-4">
        Support cases
      </h2>
      {cases.data.cases.length === 0 ? (
        <NewCaseForm transactionId={transactionId} />
      ) : (
        <div className="divide-y divide-slate-100">
          <ul className="divide-y divide-slate-100">
            {openCases.map((c) => (
              <CaseCard key={c.case_id} caseItem={c} />
            ))}
          </ul>
          {closedCases.length > 0 && (
            <details className="p-4">
              <summary className="cursor-pointer text-xs font-medium text-slate-500">
                Closed cases ({closedCases.length})
              </summary>
              <ul className="mt-2 divide-y divide-slate-100">
                {closedCases.map((c) => (
                  <CaseCard key={c.case_id} caseItem={c} />
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
