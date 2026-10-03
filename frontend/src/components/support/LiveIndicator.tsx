import type { SupportStreamStatus } from "../../hooks/useSupportStream";

const STATUS_META: Record<
  SupportStreamStatus,
  { label: string; dotClasses: string; chipClasses: string; title: string }
> = {
  live: {
    label: "Live",
    dotClasses: "bg-emerald-500",
    chipClasses: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
    title: "Connected to the live event stream (server-sent events). Updates arrive as the backend publishes them.",
  },
  connecting: {
    label: "Connecting…",
    dotClasses: "bg-slate-400 animate-pulse",
    chipClasses: "bg-slate-100 text-slate-600 ring-slate-500/20",
    title: "Opening the live event stream…",
  },
  reconnecting: {
    label: "Reconnecting…",
    dotClasses: "bg-amber-500 animate-pulse",
    chipClasses: "bg-amber-50 text-amber-700 ring-amber-600/20",
    title: "The live stream dropped — retrying with a fresh ticket. The list below may be stale until it reconnects.",
  },
  error: {
    label: "Live updates off",
    dotClasses: "bg-red-500",
    chipClasses: "bg-red-50 text-red-700 ring-red-600/20",
    title: "Live updates are not available (permission denied or unsupported). Data shown is from the last load; refresh to update.",
  },
};

/** Honest connection chip for the support SSE feed — a stale list must never
 * look live. Refetches still work; only the push channel is affected. */
export function LiveIndicator({ status }: { status: SupportStreamStatus }) {
  const meta = STATUS_META[status];
  return (
    <span
      data-testid="live-indicator"
      title={meta.title}
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${meta.chipClasses}`}
    >
      <span aria-hidden="true" className={`h-2 w-2 rounded-full ${meta.dotClasses}`} />
      {meta.label}
    </span>
  );
}
