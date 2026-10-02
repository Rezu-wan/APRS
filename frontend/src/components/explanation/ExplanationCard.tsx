import { useState } from "react";
import { Loader2, MessageSquareText } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { useExplanation } from "../../hooks/useMutations";
import { ApiError } from "../../api/client";
import type { Audience, ExplanationResponse, Language } from "../../types/api";

export function ExplanationCard({ transactionId }: { transactionId: string }) {
  const { user } = useAuth();
  const isCustomer = user?.role === "CUSTOMER";
  const defaultAudience: Audience =
    user?.role === "SUPPORT" || user?.role === "ADMIN" ? "support" : "customer";

  const [language, setLanguage] = useState<Language>("bn"); // Bangladesh-first
  const [audience, setAudience] = useState<Audience>(defaultAudience);
  const [result, setResult] = useState<ExplanationResponse | null>(null);

  const explanation = useExplanation(transactionId);

  function handleGenerate() {
    // CUSTOMER is locked to Bangla + customer audience; the backend enforces this too.
    explanation.mutate(
      {
        language: isCustomer ? "bn" : language,
        audience: isCustomer ? "customer" : audience,
      },
      { onSuccess: (data) => setResult(data) }
    );
  }

  const error =
    explanation.error instanceof ApiError
      ? explanation.error.message
      : explanation.error
        ? "Could not generate the explanation. Please try again."
        : null;

  return (
    <section
      aria-labelledby="explanation-heading"
      className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
    >
      <h2
        id="explanation-heading"
        className="flex items-center gap-2 text-sm font-semibold text-slate-900"
      >
        <MessageSquareText aria-hidden="true" className="h-4 w-4 text-indigo-600" />
        Explanation
      </h2>

      {isCustomer && (
        <p className="mt-2 text-xs text-slate-500">
          Explanations are shown in Bangla and written for customers.
        </p>
      )}

      <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center">
        {!isCustomer && (
          <div
            role="group"
            aria-label="Explanation language"
            className="inline-flex rounded-md border border-slate-300 bg-white p-0.5"
          >
            {(
              [
                ["bn", "বাংলা"],
                ["en", "English"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={language === value}
                onClick={() => setLanguage(value)}
                className={`rounded px-3 py-1 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
                  language === value
                    ? "bg-indigo-600 text-white"
                    : "text-slate-600 hover:text-slate-900"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        )}

        {!isCustomer && (
          <div>
            <label htmlFor="explanation-audience" className="sr-only">
              Explanation audience
            </label>
            <select
              id="explanation-audience"
              value={audience}
              onChange={(event) => setAudience(event.target.value as Audience)}
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
            >
              <option value="customer">Customer</option>
              <option value="support">Support</option>
            </select>
          </div>
        )}

        <div className="sm:ml-auto">
          <button
            type="button"
            onClick={handleGenerate}
            disabled={explanation.isPending}
            className="inline-flex items-center gap-2 rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
          >
            {explanation.isPending && (
              <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
            )}
            {explanation.isPending ? "Generating…" : "Generate explanation"}
          </button>
        </div>
      </div>

      {error && (
        <div
          role="alert"
          className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800"
        >
          {error}
        </div>
      )}

      {result && (
        <div className="mt-4 rounded-lg border border-indigo-100 bg-indigo-50/50 p-4">
          {result.is_fallback && (
            <span className="mb-2 inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500 ring-1 ring-inset ring-slate-500/20">
              Standard explanation
            </span>
          )}
          <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-900">
            {result.explanation}
          </p>
          <p className="mt-3 text-xs text-slate-400">
            {result.provider} · prompt v{result.prompt_version}
            {result.cached ? " · cached" : ""}
          </p>
        </div>
      )}

      {!result && !error && !explanation.isPending && (
        <p className="mt-3 text-sm text-slate-500">
          Generate a plain-language explanation of what happened to this transaction.
        </p>
      )}
    </section>
  );
}
