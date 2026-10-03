import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { ChevronLeft, ChevronRight, RefreshCcw, Search } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { useTransactionList } from "../hooks/useQueries";
import { ApiError } from "../api/client";
import { ErrorState } from "../components/ui/ErrorState";
import { EmptyState } from "../components/ui/EmptyState";
import { StatusBadge } from "../components/transaction/StatusBadge";
import { formatAmount } from "../components/transaction/TransactionSummary";
import type { Transaction, TransactionState } from "../types/api";

const searchSchema = z
  .string()
  .trim()
  .min(1, "Enter a transaction ID.")
  .max(64, "Transaction IDs are at most 64 characters.");

export default function TransactionSearch() {
  const { user } = useAuth();
  const isCustomer = user?.role === "CUSTOMER";
  // Customers get their own transaction list (role-scoped server-side);
  // staff keep the exact-ID lookup form.
  if (isCustomer) {
    return <CustomerTransactionList />;
  }
  return <StaffIdLookup />;
}

function StaffIdLookup() {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const parsed = searchSchema.safeParse(value);
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Enter a transaction ID.");
      return;
    }
    setError(null);
    navigate(`/transactions/${encodeURIComponent(parsed.data)}`);
  }

  return (
    <div className="mx-auto flex max-w-lg flex-col items-center px-4 py-16">
      <h1 className="text-lg font-semibold text-slate-900">Find a transaction</h1>
      <p className="mt-1 text-center text-sm text-slate-500">
        Enter a transaction ID to view its status, timeline, and recovery details.
      </p>

      <form onSubmit={handleSubmit} role="search" className="mt-6 w-full">
        <label htmlFor="transaction-search-input" className="sr-only">
          Transaction ID
        </label>
        <div className="flex gap-2">
          <div className="relative min-w-0 flex-1">
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
            />
            <input
              id="transaction-search-input"
              type="text"
              value={value}
              onChange={(event) => {
                setValue(event.target.value);
                setError(null);
              }}
              placeholder="Transaction ID"
              maxLength={64}
              aria-invalid={error !== null}
              aria-describedby={error ? "transaction-search-error" : "transaction-search-hint"}
              className={`w-full rounded-md border bg-white py-2 pl-9 pr-3 font-mono text-sm text-slate-900 placeholder:font-sans placeholder:text-slate-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
                error ? "border-red-400" : "border-slate-300"
              }`}
            />
          </div>
          <button
            type="submit"
            className="shrink-0 rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
          >
            Search
          </button>
        </div>
        {error ? (
          <p id="transaction-search-error" role="alert" className="mt-2 text-sm text-red-600">
            {error}
          </p>
        ) : (
          <p id="transaction-search-hint" className="mt-2 text-xs text-slate-500">
            IDs are up to 64 characters, e.g.{" "}
            <span className="font-mono">TXN-20260930-000123</span>.
          </p>
        )}
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Customer transaction list — the customer's OWN transactions, server-filtered
// by status and date range (GET /transactions scope is enforced backend-side),
// paginated, with an in-page ID/merchant filter.
// ---------------------------------------------------------------------------

const PAGE_SIZE = 10;

/** Filter dropdown options — fixed state list with humanized labels. */
const STATE_OPTIONS: Array<[TransactionState, string]> = [
  ["SUCCESS", "Success"],
  ["FAILED", "Failed"],
  ["STALLED", "Stalled"],
  ["PROCESSING", "Processing"],
  ["INITIATED", "Initiated"],
  ["RISK_ASSESSED", "Risk assessed"],
  ["RECOVERY_PENDING", "Recovery pending"],
  ["LIMIT_RELEASED", "Limit released"],
  ["MANUAL_REVIEW", "Manual review"],
  ["RECOVERY_REJECTED", "Recovery rejected"],
];

function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

/** A UTC day bounds conversion for the backend's datetime range params. */
function dayBounds(day: string): { from?: string; to?: string } {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return {};
  return {
    from: `${day}T00:00:00.000Z`,
    to: `${day}T23:59:59.999Z`,
  };
}

function CustomerTransactionList() {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [dayFrom, setDayFrom] = useState("");
  const [dayTo, setDayTo] = useState("");
  const [textFilter, setTextFilter] = useState("");
  const [offset, setOffset] = useState(0);

  // Server-side filters: status + date range (the backend filters before
  // paginating, so counts stay correct at any volume).
  const boundsFrom = dayBounds(dayFrom).from;
  const boundsTo = dayBounds(dayTo).to;
  const params = useMemo(
    () => ({
      limit: PAGE_SIZE,
      offset,
      state: statusFilter === "" ? undefined : statusFilter,
      date_from: boundsFrom,
      date_to: boundsTo,
    }),
    [offset, statusFilter, boundsFrom, boundsTo]
  );
  const list = useTransactionList(params);

  const hasServerFilters = statusFilter !== "" || boundsFrom !== undefined || boundsTo !== undefined;
  const filtersActive = hasServerFilters || textFilter.trim().length > 0;

  function resetPage() {
    setOffset(0);
  }

  function clearFilters() {
    setStatusFilter("");
    setDayFrom("");
    setDayTo("");
    setTextFilter("");
    resetPage();
  }

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ["transactions"] });
  }

  // In-page text filter over the loaded page (ID, merchant id, or resolved
  // merchant name substring).
  const needle = textFilter.trim().toLowerCase();
  const visible: Transaction[] = useMemo(() => {
    const items = list.data?.items ?? [];
    if (needle.length === 0) return items;
    return items.filter(
      (tx) =>
        tx.transaction_id.toLowerCase().includes(needle) ||
        tx.merchant_id.toLowerCase().includes(needle) ||
        (tx.merchant_name ?? "").toLowerCase().includes(needle)
    );
  }, [list.data, needle]);

  const total = list.data?.total ?? 0;
  const shownCount = visible.length;
  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = offset + shownCount;

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-lg font-semibold text-slate-900">My transactions</h1>
          <p className="mt-1 text-sm text-slate-500">
            Your transactions, newest first. Open one for its full timeline and recovery status.
          </p>
        </div>
        <button
          type="button"
          onClick={refresh}
          disabled={list.isFetching}
          className="inline-flex shrink-0 items-center gap-2 self-start rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
        >
          <RefreshCcw aria-hidden="true" className={`h-4 w-4${list.isFetching ? " animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {/* Filters — status + dates hit the backend; text filters the loaded page. */}
      <div
        role="search"
        className="grid grid-cols-1 gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:grid-cols-2 lg:grid-cols-4"
      >
        <div>
          <label htmlFor="tx-filter-text" className="block text-xs font-medium text-slate-600">
            Filter by ID or merchant
          </label>
          <input
            id="tx-filter-text"
            type="text"
            value={textFilter}
            onChange={(event) => setTextFilter(event.target.value)}
            placeholder="e.g. TXN or MERCHANT-DEMO"
            maxLength={64}
            className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          />
        </div>
        <div>
          <label htmlFor="tx-filter-status" className="block text-xs font-medium text-slate-600">
            Status
          </label>
          <select
            id="tx-filter-status"
            value={statusFilter}
            onChange={(event) => {
              setStatusFilter(event.target.value);
              resetPage();
            }}
            className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          >
            <option value="">All statuses</option>
            {STATE_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="tx-filter-from" className="block text-xs font-medium text-slate-600">
            From date
          </label>
          <input
            id="tx-filter-from"
            type="date"
            value={dayFrom}
            max={dayTo || undefined}
            onChange={(event) => {
              setDayFrom(event.target.value);
              resetPage();
            }}
            className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          />
        </div>
        <div>
          <label htmlFor="tx-filter-to" className="block text-xs font-medium text-slate-600">
            To date
          </label>
          <input
            id="tx-filter-to"
            type="date"
            value={dayTo}
            min={dayFrom || undefined}
            onChange={(event) => {
              setDayTo(event.target.value);
              resetPage();
            }}
            className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          />
        </div>
      </div>

      {list.isPending ? (
        <div aria-hidden="true" className="animate-pulse space-y-2">
          {Array.from({ length: 5 }, (_, i) => (
            <div key={i} className="h-14 rounded-lg border border-slate-200 bg-white" />
          ))}
        </div>
      ) : list.isError ? (
        <ErrorState
          title="Transactions unavailable"
          message={
            list.error instanceof ApiError
              ? list.error.message
              : "Could not load your transactions."
          }
          error={list.error}
          onRetry={() => void list.refetch()}
        />
      ) : total === 0 && !filtersActive ? (
        <EmptyState
          title="No transactions yet"
          message="Payments processed through the platform will appear here once they are recorded."
        />
      ) : shownCount === 0 ? (
        <EmptyState
          title="No transactions match"
          message={
            needle.length > 0
              ? `Nothing on this page matches “${textFilter.trim()}”.`
              : "No transactions match the selected filters."
          }
        >
          <button
            type="button"
            onClick={clearFilters}
            className="mt-2 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
          >
            Clear filters
          </button>
        </EmptyState>
      ) : (
        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <ul className="divide-y divide-slate-100">
            {visible.map((tx) => (
              <li key={tx.transaction_id}>
                <Link
                  to={`/transactions/${encodeURIComponent(tx.transaction_id)}`}
                  className="flex items-center gap-3 px-4 py-3 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-500 sm:px-6"
                >
                  <StatusBadge state={tx.current_state} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-slate-900">
                      {tx.merchant_name ?? (tx.merchant_id || "No merchant")}
                      {tx.merchant_category ? (
                        <span className="text-slate-400"> · {tx.merchant_category}</span>
                      ) : null}
                    </span>
                    <span className="block truncate font-mono text-xs text-slate-500">
                      {tx.transaction_id} · {formatDate(tx.timestamp)}
                      {tx.failure_reason ? ` · ${tx.failure_reason}` : ""}
                    </span>
                  </span>
                  <span className="shrink-0 text-sm font-medium tabular-nums text-slate-900">
                    {formatAmount(tx.amount, tx.currency)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>

          {/* Pagination — server-side offset paging over the filtered set. */}
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 px-4 py-3 sm:px-6">
            <p className="text-xs text-slate-500" aria-live="polite">
              {needle.length > 0
                ? `${shownCount} of ${list.data?.items.length ?? 0} on this page match “${textFilter.trim()}”`
                : total > shownCount || offset > 0
                  ? `Showing ${rangeStart}–${rangeEnd} of ${total.toLocaleString()}`
                  : `${total.toLocaleString()} transaction${total === 1 ? "" : "s"}`}
            </p>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                disabled={offset === 0}
                className="inline-flex items-center gap-1 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
              >
                <ChevronLeft aria-hidden="true" className="h-4 w-4" />
                Previous
              </button>
              <button
                type="button"
                onClick={() => setOffset(offset + PAGE_SIZE)}
                disabled={offset + PAGE_SIZE >= total}
                className="inline-flex items-center gap-1 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
              >
                Next
                <ChevronRight aria-hidden="true" className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
