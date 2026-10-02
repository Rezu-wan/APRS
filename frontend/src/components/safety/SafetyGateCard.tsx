import { Check, Circle, X } from "lucide-react";
import { humanizeBlockedReason } from "../../api/demo";
import { useReconstruction, useRecovery, useRiskAssessment, useTransaction } from "../../hooks/useQueries";
import type { AnomalyType } from "../../types/api";

// ---------------------------------------------------------------------------
// Display-only rendering of the executor's independent safety gate. Every row
// is derived from real data — an unknown condition renders "—", never a ✓.
// ---------------------------------------------------------------------------

type RowState = "pass" | "fail" | "unknown";

interface GateRow {
  label: string;
  state: RowState;
}

function RowIcon({ state }: { state: RowState }) {
  if (state === "pass") {
    return (
      <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-emerald-100">
        <Check aria-hidden="true" className="h-3 w-3 text-emerald-700" />
      </span>
    );
  }
  if (state === "fail") {
    return (
      <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-red-100">
        <X aria-hidden="true" className="h-3 w-3 text-red-700" />
      </span>
    );
  }
  return (
    <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center">
      <Circle aria-hidden="true" className="h-2 w-2 text-slate-300" />
    </span>
  );
}

const ROW_STATE_TEXT: Record<RowState, string> = {
  pass: "text-slate-800",
  fail: "text-slate-800",
  unknown: "text-slate-400",
};

export function SafetyGateCard({ transactionId }: { transactionId: string }) {
  const transactionQuery = useTransaction(transactionId);
  const reconstructionQuery = useReconstruction(transactionId);
  const riskQuery = useRiskAssessment(transactionId);
  const recoveryQuery = useRecovery(transactionId);

  const transaction = transactionQuery.data ?? null;
  const reconstruction = reconstructionQuery.data ?? null;
  const assessment = riskQuery.data?.assessment ?? null;
  const record = recoveryQuery.data ?? null;

  const anomaly = assessment?.anomaly_type as AnomalyType | undefined;
  const processed = record !== null;

  // Row derivations — every condition comes from real query data only.
  const rows: GateRow[] = [
    {
      label: "Transaction not already successful",
      state: transaction ? (transaction.current_state !== "SUCCESS" ? "pass" : "fail") : "unknown",
    },
    {
      label: "Settlement not confirmed",
      state: reconstruction
        ? reconstruction.settlement_status !== "CONFIRMED"
          ? "pass"
          : "fail"
        : "unknown",
    },
    {
      label: "Single debit",
      // Debit observed (reconstruction exists) without double-debit evidence.
      state: reconstruction
        ? anomaly === "DOUBLE_DEDUCTION"
          ? "fail"
          : "pass"
        : "unknown",
    },
    {
      label: "Risk policy permits recovery",
      state: assessment ? (assessment.recovery_candidate ? "pass" : "fail") : "unknown",
    },
    {
      label: "Risk level acceptable",
      state: assessment
        ? ["LOW", "MEDIUM"].includes(assessment.risk_level)
          ? "pass"
          : "fail"
        : "unknown",
    },
    {
      label: "Evidence sufficient",
      state:
        reconstruction && assessment
          ? !["INCOMPLETE", "UNKNOWN"].includes(anomaly ?? "")
            ? "pass"
            : "fail"
          : "unknown",
    },
    {
      label: "Idempotency key bound",
      // Only meaningful once the recovery row exists; "—" before processing.
      state: processed ? (record.idempotency_key ? "pass" : "fail") : "unknown",
    },
    {
      label: "Provider state valid",
      state: processed ? (record.status !== "FAILED" ? "pass" : "fail") : "unknown",
    },
  ];

  // Gate outcome banner — mirrors the executor's own verdict, never invented.
  const verified = record?.status === "VERIFIED";
  let banner: { label: string; className: string };
  if (verified) {
    banner = {
      label: "APPROVED",
      className: "bg-emerald-50 text-emerald-800 ring-emerald-600/20",
    };
  } else if (record?.blocked_reason) {
    banner = {
      label: `BLOCKED — ${humanizeBlockedReason(record.blocked_reason)}`,
      className: "bg-red-50 text-red-800 ring-red-600/20",
    };
  } else {
    banner = {
      label: "NOT YET EVALUATED",
      className: "bg-slate-100 text-slate-600 ring-slate-500/20",
    };
  }

  return (
    <section
      aria-labelledby="safety-gate-heading"
      className="judge-enlarge rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
      data-testid="safety-gate-card"
    >
      <h2 id="safety-gate-heading" className="text-sm font-semibold text-slate-900">
        Safety gate
      </h2>
      <p className="mt-1 text-xs text-slate-500">
        An independent gate re-checks fresh evidence inside the executor before any release.
      </p>

      <ul className="mt-4 divide-y divide-slate-100">
        {rows.map((row) => (
          <li key={row.label} className="flex items-center gap-2.5 py-2">
            <RowIcon state={row.state} />
            <span className={`text-sm ${ROW_STATE_TEXT[row.state]}`}>{row.label}</span>
            {row.state === "unknown" && (
              <span aria-hidden="true" className="ml-auto text-sm text-slate-400">
                —
              </span>
            )}
          </li>
        ))}
      </ul>

      <div className="mt-4">
        <span
          className={`inline-flex items-center rounded-full px-3 py-1 text-sm font-semibold ring-1 ring-inset ${banner.className}`}
        >
          {banner.label}
        </span>
      </div>
    </section>
  );
}
