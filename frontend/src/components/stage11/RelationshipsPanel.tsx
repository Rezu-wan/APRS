// Stage 11D — relationship graph signals. Descriptions are rendered verbatim
// (the backend keeps them structural); advisory-only separation is visible.
import { ApiError } from "../../api/client";
import type { RelationshipReport } from "../../api/stage11";
import { useRelationships } from "../../hooks/useStage11";
import { ErrorState } from "../ui/ErrorState";
import { LevelChip } from "./BehavioralSignalsPanel";

const MAX_ENTITY_CHIPS = 8;
const MAX_EVIDENCE_CHIPS = 4;

function EntityChips({ entities }: { entities: RelationshipReport["entities"] }) {
  const visible = entities.slice(0, MAX_ENTITY_CHIPS);
  const hidden = entities.length - visible.length;
  return (
    <div className="flex flex-wrap gap-1.5">
      {visible.map((entity, index) => (
        <span
          key={`${entity.type}-${entity.id}-${index}`}
          className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-600"
        >
          {entity.type}:{entity.id}
        </span>
      ))}
      {hidden > 0 && (
        <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-400">
          +{hidden} more
        </span>
      )}
    </div>
  );
}

function EvidenceChips({ evidence }: { evidence: string[] }) {
  const visible = evidence.slice(0, MAX_EVIDENCE_CHIPS);
  const hidden = evidence.length - visible.length;
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1">
      {visible.map((id) => (
        <span key={id} className="rounded bg-slate-50 px-1.5 py-0.5 font-mono text-xs text-slate-400">
          {id}
        </span>
      ))}
      {hidden > 0 && (
        <span className="font-mono text-xs text-slate-400">+{hidden}</span>
      )}
    </div>
  );
}

function RelationshipBody({ report }: { report: RelationshipReport }) {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Entities</h3>
        <div className="mt-2">
          <EntityChips entities={report.entities} />
        </div>
      </div>

      <ul className="divide-y divide-slate-100">
        {report.signals.map((signal) => (
          <li key={signal.code} className="py-2.5">
            <div className="flex items-center gap-2">
              <LevelChip level={signal.level} />
              <span className="text-sm font-medium text-slate-800">{signal.label}</span>
              {signal.count !== null && (
                <span className="font-mono text-xs text-slate-500 tabular-nums">
                  count: {signal.count}
                </span>
              )}
            </div>
            <p className="mt-1 text-xs text-slate-500">{signal.description}</p>
            {signal.evidence.length > 0 && <EvidenceChips evidence={signal.evidence} />}
          </li>
        ))}
      </ul>

      <p className="border-t border-slate-100 pt-3 text-xs text-slate-400">
        <span className="font-mono">{report.feature_version}</span>
        {" · "}
        Advisory only — signals never bypass the recovery policy or safety gate.
      </p>
    </div>
  );
}

export function RelationshipsPanel({ transactionId }: { transactionId: string }) {
  const relationshipsQuery = useRelationships(transactionId);

  return (
    <section
      aria-labelledby="relationships-heading"
      className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
      data-testid="relationships-panel"
    >
      <h2 id="relationships-heading" className="text-sm font-semibold text-slate-900">
        Relationship signals
      </h2>

      <div className="mt-4">
        {relationshipsQuery.isPending ? (
          <div aria-hidden="true" className="animate-pulse space-y-3 py-2">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-4 w-2/3 rounded bg-slate-200" />
            ))}
          </div>
        ) : relationshipsQuery.isError ? (
          relationshipsQuery.error instanceof ApiError && relationshipsQuery.error.status === 403 ? (
            <p className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-500">
              Relationship signals are available to staff roles.
            </p>
          ) : (
            <ErrorState
              title="Relationship signals unavailable"
              message={
                relationshipsQuery.error instanceof ApiError
                  ? relationshipsQuery.error.message
                  : "Could not load the relationship signals."
              }
              onRetry={() => void relationshipsQuery.refetch()}
            />
          )
        ) : relationshipsQuery.data ? (
          <RelationshipBody report={relationshipsQuery.data.report} />
        ) : null}
      </div>
    </section>
  );
}
