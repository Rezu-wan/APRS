import { AlertTriangle } from "lucide-react";
import { ApiError } from "../../api/client";

interface ErrorStateProps {
  title?: string;
  message: string;
  onRetry?: () => void;
  /** Stage 9: server correlation id, quoted against server logs during
   * investigations. Pass an ApiError and its requestId is used automatically. */
  error?: unknown;
  requestId?: string | null;
}

export function ErrorState({
  title = "Something went wrong",
  message,
  onRetry,
  error,
  requestId,
}: ErrorStateProps) {
  const correlationId = requestId ?? (error instanceof ApiError ? error.requestId : undefined);
  return (
    <div role="alert" className="flex flex-col items-center gap-3 py-16 text-center">
      <AlertTriangle aria-hidden="true" className="h-10 w-10 text-red-500" />
      <h2 className="text-base font-semibold text-slate-900">{title}</h2>
      <p className="max-w-md text-sm text-slate-500">{message}</p>
      {correlationId && (
        <p className="font-mono text-xs text-slate-400">Request ID: {correlationId}</p>
      )}
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-1 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
        >
          Try again
        </button>
      )}
    </div>
  );
}
