import { CheckCircle2, Circle, XCircle, type LucideIcon } from "lucide-react";
import { useReconstruction, useRecovery, useRiskAssessment } from "../../hooks/useQueries";

// ---------------------------------------------------------------------------
// Display-only pipeline visualization. Every step is derived from real query
// data — pending states stay pending, nothing is fabricated.
// ---------------------------------------------------------------------------

type StepState = "done" | "pending" | "blocked";

interface Step {
  key: string;
  label: string;
  state: StepState;
  tooltip: string;
}

const STEP_STATE_META: Record<StepState, { icon: LucideIcon; className: string }> = {
  done: { icon: CheckCircle2, className: "text-emerald-600" },
  pending: { icon: Circle, className: "text-slate-300" },
  blocked: { icon: XCircle, className: "text-red-600" },
};

export function RecoveryPipeline({ transactionId }: { transactionId: string }) {
  const reconstructionQuery = useReconstruction(transactionId);
  const riskQuery = useRiskAssessment(transactionId);
  const recoveryQuery = useRecovery(transactionId);

  const reconstruction = reconstructionQuery.data ?? null;
  const assessment = riskQuery.data?.assessment ?? null;
  const record = recoveryQuery.data ?? null;

  const blocked = record?.blocked_reason != null;
  const verified = record?.status === "VERIFIED";

  // Honest derivation — a step is only ✓ when its real data says so.
  const steps: Step[] = [
    {
      key: "EVENTS",
      label: "Events",
      // A successful reconstruction read implies payment events were ingested.
      state: reconstruction ? "done" : "pending",
      tooltip: "Simulated payment events ingested from the sandbox provider.",
    },
    {
      key: "RECONSTRUCTION",
      label: "Reconstruction",
      state: reconstruction ? "done" : "pending",
      tooltip: "Payment flow reconstructed from the event log.",
    },
    {
      key: "RISK",
      label: "Risk",
      state: assessment ? "done" : "pending",
      tooltip: "Hybrid rules + ML risk assessment completed.",
    },
    {
      key: "POLICY",
      label: "Policy",
      // The deterministic policy decision exists once a recovery row exists.
      state: record ? "done" : "pending",
      tooltip: "Deterministic recovery policy decision made.",
    },
    {
      key: "SAFETY",
      label: "Safety",
      state: !record ? "pending" : blocked ? "blocked" : "done",
      tooltip: "Independent safety gate re-checked fresh evidence before release.",
    },
    {
      key: "EXECUTOR",
      label: "Executor",
      state: blocked ? "blocked" : record?.provider_reference ? "done" : "pending",
      tooltip: "Sandbox provider executed the simulated limit release.",
    },
    {
      key: "VERIFICATION",
      label: "Verification",
      state: verified ? "done" : "pending",
      tooltip: "Post-execution verification passed (VERIFIED).",
    },
  ];

  // Final-state chip — exactly one honest outcome.
  let finalChip: { label: string; className: string };
  if (verified) {
    finalChip = {
      label: "VERIFIED",
      className: "bg-emerald-50 text-emerald-700 ring-emerald-600/20 font-bold",
    };
  } else if (blocked) {
    finalChip = {
      label: "BLOCKED",
      className: "bg-red-50 text-red-700 ring-red-600/20 font-bold",
    };
  } else {
    finalChip = {
      label: "READY — awaiting recovery",
      className: "bg-slate-100 text-slate-600 ring-slate-500/20",
    };
  }

  return (
    <section
      aria-labelledby="recovery-pipeline-heading"
      className="judge-enlarge rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
      data-testid="recovery-pipeline"
    >
      <h2 id="recovery-pipeline-heading" className="text-sm font-semibold text-slate-900">
        Recovery pipeline
      </h2>

      {/* Horizontal on desktop, vertical on mobile */}
      <ol className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-1">
        {steps.map((step, index) => {
          const meta = STEP_STATE_META[step.state];
          const Icon = meta.icon;
          return (
            <li key={step.key} className="flex min-w-0 items-center gap-2 sm:flex-1">
              <span
                title={step.tooltip}
                className="inline-flex shrink-0 items-center gap-1.5 text-xs font-medium"
              >
                <Icon aria-hidden="true" className={`h-4 w-4 ${meta.className}`} />
                <span
                  className={
                    step.state === "done"
                      ? "text-emerald-700"
                      : step.state === "blocked"
                        ? "text-red-700"
                        : "text-slate-500"
                  }
                >
                  {step.label}
                </span>
              </span>
              {index < steps.length - 1 && (
                <span
                  aria-hidden="true"
                  className="hidden h-px min-w-4 flex-1 bg-slate-200 sm:block"
                />
              )}
            </li>
          );
        })}
      </ol>

      <div className="mt-4">
        <span
          className={`inline-flex items-center rounded-full px-3 py-1 text-sm ring-1 ring-inset ${finalChip.className}`}
        >
          {finalChip.label}
        </span>
      </div>
    </section>
  );
}
