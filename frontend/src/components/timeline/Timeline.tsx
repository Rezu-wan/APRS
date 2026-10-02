import {
  AlertTriangle,
  CheckCircle2,
  CircleDot,
  Clock,
  Pause,
  ShieldCheck,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import type { TimelineEvent } from "../../types/api";

interface EventIcon {
  Icon: LucideIcon;
  className: string;
}

/** Best-effort icon for free-form event types; unknown types get a plain dot. */
function iconForEventType(eventType: string): EventIcon {
  const t = eventType.toLowerCase();
  if (t.includes("fail") || t.includes("reject")) return { Icon: XCircle, className: "text-red-500" };
  if (t.includes("release") || t.includes("success")) return { Icon: CheckCircle2, className: "text-emerald-500" };
  if (t.includes("review") || t.includes("manual")) return { Icon: AlertTriangle, className: "text-amber-500" };
  if (t.includes("risk")) return { Icon: ShieldCheck, className: "text-violet-500" };
  if (t.includes("stall") || t.includes("pause")) return { Icon: Pause, className: "text-amber-500" };
  if (t.includes("initiat") || t.includes("creat") || t.includes("start")) return { Icon: Clock, className: "text-sky-500" };
  return { Icon: CircleDot, className: "text-slate-400" };
}

function humanizeEventType(eventType: string): string {
  return eventType
    .toLowerCase()
    .split(/[_\s-]+/)
    .map((word) => (word.length > 0 ? word[0].toUpperCase() + word.slice(1) : word))
    .join(" ");
}

function formatDateTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function EventRow({ event }: { event: TimelineEvent }) {
  const { Icon, className: iconClass } = iconForEventType(event.event_type);
  return (
    <li className="relative pb-6 pl-8 last:pb-0">
      {/* Dot on the vertical rail */}
      <span
        aria-hidden="true"
        className="absolute left-0 top-1 flex h-5 w-5 items-center justify-center rounded-full bg-white ring-1 ring-slate-200"
      >
        <Icon aria-hidden="true" className={`h-3.5 w-3.5 ${iconClass}`} />
      </span>
      <div className="flex flex-col gap-1">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
          <span className="text-sm font-medium text-slate-900">
            {humanizeEventType(event.event_type)}
          </span>
          <time className="text-xs text-slate-500">{formatDateTime(event.timestamp)}</time>
        </div>
        <p className="font-mono text-xs text-slate-500">
          {event.previous_state ?? "—"} → {event.new_state}
        </p>
        {event.reason && <p className="text-sm text-slate-600">{event.reason}</p>}
        {(event.risk_score !== null || event.safe_to_release_probability !== null) && (
          <div className="flex flex-wrap gap-1.5 pt-0.5">
            {event.failure_prediction && (
              <span className="inline-flex items-center rounded-full bg-violet-50 px-2 py-0.5 text-xs font-medium text-violet-700 ring-1 ring-inset ring-violet-600/20">
                {event.failure_prediction}
              </span>
            )}
            {event.risk_score !== null && (
              <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700 tabular-nums">
                risk {event.risk_score.toFixed(2)}
              </span>
            )}
            {event.safe_to_release_probability !== null && (
              <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700 tabular-nums">
                safe {new Intl.NumberFormat("en", { style: "percent", maximumFractionDigits: 0 }).format(event.safe_to_release_probability)}
              </span>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

export function Timeline({ events }: { events: TimelineEvent[] }) {
  if (events.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-slate-500">
        No timeline events recorded for this transaction yet.
      </p>
    );
  }
  return (
    <ol className="max-h-[32rem] overflow-y-auto border-l border-slate-200 pl-5">
      {events.map((event) => (
        <EventRow key={event.event_id} event={event} />
      ))}
    </ol>
  );
}
