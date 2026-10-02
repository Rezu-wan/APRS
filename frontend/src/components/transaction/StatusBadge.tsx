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
import type { TransactionState } from "../../types/api";

interface StateMeta {
  label: string;
  icon: LucideIcon;
  /** bg + text + ring classes — always paired with an icon and text label (never color-only). */
  classes: string;
}

const STATE_META: Record<TransactionState, StateMeta> = {
  INITIATED: {
    label: "Initiated",
    icon: Clock,
    classes: "bg-slate-100 text-slate-700 ring-slate-500/20",
  },
  PROCESSING: {
    label: "Processing",
    icon: Clock,
    classes: "bg-sky-50 text-sky-700 ring-sky-600/20",
  },
  SUCCESS: {
    label: "Success",
    icon: CheckCircle2,
    classes: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  },
  FAILED: {
    label: "Failed",
    icon: XCircle,
    classes: "bg-red-50 text-red-700 ring-red-600/20",
  },
  STALLED: {
    label: "Stalled",
    icon: Pause,
    classes: "bg-amber-50 text-amber-700 ring-amber-600/20",
  },
  RISK_ASSESSED: {
    label: "Risk assessed",
    icon: ShieldCheck,
    classes: "bg-violet-50 text-violet-700 ring-violet-600/20",
  },
  RECOVERY_PENDING: {
    label: "Recovery pending",
    icon: Clock,
    classes: "bg-indigo-50 text-indigo-700 ring-indigo-600/20",
  },
  LIMIT_RELEASED: {
    label: "Limit released",
    icon: CheckCircle2,
    classes: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  },
  MANUAL_REVIEW: {
    label: "Manual review",
    icon: AlertTriangle,
    classes: "bg-amber-50 text-amber-700 ring-amber-600/20",
  },
  RECOVERY_REJECTED: {
    label: "Recovery rejected",
    icon: XCircle,
    classes: "bg-red-50 text-red-700 ring-red-600/20",
  },
};

const NEUTRAL_META: StateMeta = {
  label: "Unknown",
  icon: CircleDot,
  classes: "bg-slate-100 text-slate-600 ring-slate-500/20",
};

export function StatusBadge({ state }: { state: string }) {
  const meta = STATE_META[state as TransactionState] ?? {
    ...NEUTRAL_META,
    label: state, // Render the raw state string — do not crash on new backend states.
  };
  const Icon = meta.icon;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${meta.classes}`}
    >
      <Icon aria-hidden="true" className="h-3.5 w-3.5" />
      {meta.label}
    </span>
  );
}
