import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { KeyRound, Loader2 } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { ApiError } from "../api/client";

export function Login() {
  const { login, user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const from =
    typeof location.state === "object" &&
    location.state !== null &&
    "from" in location.state &&
    typeof (location.state as { from: unknown }).from === "string"
      ? (location.state as { from: string }).from
      : "/dashboard";

  if (user) {
    // `from` (ProtectedRoute's bounce target) wins over the dashboard so a
    // post-reload sign-in returns to where the session was lost — this render
    // can beat handleSubmit's own navigate() when the key-less reload bounces
    // an already-submitted form back here.
    return <Navigate to={from} replace />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!apiKey.trim() || submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      await login(apiKey.trim());
      navigate(from, { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError("Invalid API key");
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("An unexpected error occurred. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
      <div className="w-full max-w-sm">
        <div className="rounded-lg border border-slate-200 bg-white p-8 shadow-sm">
          <div className="mb-6 flex flex-col items-center gap-2 text-center">
            <span className="flex h-10 w-10 items-center justify-center rounded-full bg-indigo-50">
              <KeyRound aria-hidden="true" className="h-5 w-5 text-indigo-600" />
            </span>
            <h1 className="text-lg font-semibold tracking-tight text-slate-900">
              Payment Recovery Digital Twin
            </h1>
            <p className="text-sm text-slate-500">Sign in with your API key to continue.</p>
          </div>

          <form onSubmit={handleSubmit} noValidate>
            <label htmlFor="api-key" className="block text-sm font-medium text-slate-700">
              API key
            </label>
            <input
              id="api-key"
              type="password"
              autoComplete="off"
              aria-label="API key"
              aria-invalid={error !== null}
              aria-describedby={error ? "api-key-error" : "api-key-hint"}
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              disabled={submitting}
              className="mt-1.5 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-1 disabled:bg-slate-50"
              placeholder="Enter your API key"
            />

            {error && (
              <p
                id="api-key-error"
                role="alert"
                className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
              >
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={submitting || apiKey.trim().length === 0}
              className="mt-4 flex w-full items-center justify-center gap-2 rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting && <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />}
              {submitting ? "Signing in…" : "Sign in"}
            </button>
          </form>

          <div
            id="api-key-hint"
            className="mt-6 rounded-md bg-slate-50 border border-slate-200 px-3 py-2.5 text-xs leading-relaxed text-slate-500"
          >
            Dev API keys are configured in the backend environment. Ask your administrator if you
            don&apos;t have one.
          </div>
        </div>
      </div>
    </div>
  );
}
