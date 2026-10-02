interface RiskIndicatorProps {
  riskScore: number | null;
  safeProbability: number | null;
}

const percentFormat = new Intl.NumberFormat("en", {
  style: "percent",
  maximumFractionDigits: 0,
});

function formatPercent(value: number): string {
  return percentFormat.format(Math.min(Math.max(value, 0), 1));
}

function clamp01(value: number): number {
  return Math.min(Math.max(value, 0), 1);
}

/** Amber-to-red bar color for the risk score. */
function riskBarClass(score: number): string {
  if (score >= 0.7) return "bg-red-500";
  if (score >= 0.4) return "bg-amber-500";
  return "bg-amber-400";
}

export function RiskIndicator({ riskScore, safeProbability }: RiskIndicatorProps) {
  return (
    <div className="space-y-4">
      <div>
        <div className="mb-1 flex items-baseline justify-between gap-2">
          <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Risk score
          </span>
          <span className="text-sm font-semibold text-slate-900 tabular-nums">
            {riskScore === null ? "—" : clamp01(riskScore).toFixed(2)}
          </span>
        </div>
        <div
          role="meter"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={riskScore === null ? undefined : Math.round(clamp01(riskScore) * 100)}
          aria-label={`Risk score ${riskScore === null ? "unavailable" : clamp01(riskScore).toFixed(2)}`}
          className="h-2.5 w-full overflow-hidden rounded-full bg-slate-100"
        >
          {riskScore !== null && (
            <div
              className={`h-full rounded-full ${riskBarClass(clamp01(riskScore))}`}
              style={{ width: `${clamp01(riskScore) * 100}%` }}
            />
          )}
        </div>
      </div>

      <div>
        <div className="mb-1 flex items-baseline justify-between gap-2">
          <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Safe-release probability
          </span>
          <span className="text-sm font-semibold text-slate-900 tabular-nums">
            {safeProbability === null ? "—" : formatPercent(safeProbability)}
          </span>
        </div>
        <div
          role="meter"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={
            safeProbability === null ? undefined : Math.round(clamp01(safeProbability) * 100)
          }
          aria-label={`Safe-release probability ${
            safeProbability === null ? "unavailable" : formatPercent(safeProbability)
          }`}
          className="h-2.5 w-full overflow-hidden rounded-full bg-slate-100"
        >
          {safeProbability !== null && (
            <div
              className="h-full rounded-full bg-emerald-500"
              style={{ width: `${clamp01(safeProbability) * 100}%` }}
            />
          )}
        </div>
      </div>

      <p className="text-xs italic text-slate-500">
        Model assessment — probabilities are not guarantees.
      </p>
    </div>
  );
}
