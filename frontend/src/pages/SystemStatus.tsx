import { RoleGate } from "../components/ui/RoleGate";
import { ErrorState } from "../components/ui/ErrorState";
import { useDemoStatus } from "../hooks/useDemo";

function Dot({ tone }: { tone: "green" | "amber" | "red" }) {
  const color =
    tone === "green" ? "bg-emerald-500" : tone === "amber" ? "bg-amber-500" : "bg-red-500";
  return <span aria-hidden="true" className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${color}`} />;
}

function StatusRow({
  name,
  tone,
  children,
}: {
  name: string;
  tone: "green" | "amber" | "red";
  children: React.ReactNode;
}) {
  return (
    <div
      data-testid={`status-row-${name}`}
      className="flex items-center gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm"
    >
      <Dot tone={tone} />
      <span className="w-40 shrink-0 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {name}
      </span>
      <span className="judge-enlarge min-w-0 flex-1 text-sm font-medium text-slate-900">
        {children}
      </span>
    </div>
  );
}

function SkeletonRow() {
  return (
    <div
      aria-hidden="true"
      className="flex animate-pulse items-center gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm"
    >
      <span className="h-2.5 w-2.5 rounded-full bg-slate-200" />
      <span className="h-3 w-24 rounded bg-slate-200" />
      <span className="h-3 w-40 rounded bg-slate-100" />
    </div>
  );
}

function StatusConsole() {
  const status = useDemoStatus();

  if (status.isPending) {
    return (
      <div className="space-y-3" role="status" aria-label="Loading system status">
        <SkeletonRow />
        <SkeletonRow />
        <SkeletonRow />
        <SkeletonRow />
        <SkeletonRow />
      </div>
    );
  }

  if (status.isError || !status.data) {
    return (
      <ErrorState
        title="Status unavailable"
        message={
          status.error instanceof Error ? status.error.message : "Could not load system status."
        }
        error={status.error}
        onRetry={() => void status.refetch()}
      />
    );
  }

  const data = status.data;
  const sandbox = data.sandbox_provider;

  return (
    <div className="space-y-3">
      {/* The API is serving this very response — reachable means ONLINE. */}
      <StatusRow name="API" tone="green">
        ● ONLINE
      </StatusRow>
      <StatusRow name="DATABASE" tone={data.database === "connected" ? "green" : "red"}>
        {data.database === "connected" ? "CONNECTED" : "UNAVAILABLE"}
      </StatusRow>
      <StatusRow name="ML MODELS" tone={data.ml_models === "loaded" ? "green" : "red"}>
        {data.ml_models === "loaded" ? "LOADED" : "NOT LOADED"}
      </StatusRow>
      {data.genai.status === "available" && (
        <StatusRow name="GENAI" tone="green">
          ● AVAILABLE
        </StatusRow>
      )}
      {data.genai.status === "fallback" && (
        <StatusRow name="GENAI" tone="amber">
          ● FALLBACK MODE
          <span className="mt-0.5 block text-xs font-normal text-slate-500">
            deterministic fallback explanations — demo continues
          </span>
        </StatusRow>
      )}
      {data.genai.status === "unavailable" && (
        <StatusRow name="GENAI" tone="red">
          ● UNAVAILABLE
        </StatusRow>
      )}
      <StatusRow name="SANDBOX PROVIDER" tone="green">
        ● READY
        <span className="mt-0.5 block text-xs font-normal text-slate-500">
          {sandbox.available_limit.toLocaleString()} {sandbox.currency} available ·{" "}
          {sandbox.held_entries} held · {sandbox.released_entries} released
        </span>
      </StatusRow>
      <p className="px-1 text-xs text-slate-400">
        GenAI provider: {data.genai.provider} · prompt {data.genai.prompt_version}
      </p>
    </div>
  );
}

export default function SystemStatus() {
  return (
    <RoleGate allowed={["SYSTEM", "ADMIN", "SUPPORT"]}>
      <div className="space-y-6">
        <div>
          <h1 className="text-lg font-semibold text-slate-900">System status</h1>
          <span className="mt-1 inline-flex items-center rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-600/20">
            Simulated sandbox — no real money moves
          </span>
        </div>
        <StatusConsole />
      </div>
    </RoleGate>
  );
}
