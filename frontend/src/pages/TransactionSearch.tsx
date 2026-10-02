import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { z } from "zod";
import { Search } from "lucide-react";

const searchSchema = z
  .string()
  .trim()
  .min(1, "Enter a transaction ID.")
  .max(64, "Transaction IDs are at most 64 characters.");

export default function TransactionSearch() {
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
