// Stage 11 — temporal/behavioral/relationship/simulator/chaos API contract
// (written by the integrator; do not rename exports). All endpoints are
// staff-facing reads of the REAL system; the simulator and chaos runner can
// never mutate financial state (purity is a backend test invariant).
// SANDBOX ONLY — no real money moves.
import { z } from "zod";
import { get, post } from "./client";

// ---------------------------------------------------------------------------
// Temporal twin (11B) — GET /transactions/{id}/state-at?timestamp=
// ---------------------------------------------------------------------------

export interface TemporalStateReport {
  transaction_id: string;
  as_of: string;
  state_then: string;
  last_twin_event_type: string | null;
  observed_event_count: number;
  excluded_event_count: number;
  reconstruction: {
    root_cause: string;
    reconstruction_confidence: number;
    stages: {
      bank_debit: string;
      gateway: string;
      merchant_confirmation: string;
      settlement: string;
    };
    missing_events: string[];
  };
  uncertainty_note: string | null;
}

export const temporalStateReportSchema = z.object({
  transaction_id: z.string(),
  as_of: z.string(),
  state_then: z.string(),
  last_twin_event_type: z.string().nullable(),
  observed_event_count: z.number(),
  excluded_event_count: z.number(),
  reconstruction: z.object({
    root_cause: z.string(),
    reconstruction_confidence: z.number(),
    stages: z.object({
      bank_debit: z.string(),
      gateway: z.string(),
      merchant_confirmation: z.string(),
      settlement: z.string(),
    }),
    missing_events: z.array(z.string()),
  }),
  uncertainty_note: z.string().nullable(),
});

/** Format an ISO timestamp for the state-at query (keeps the +00:00 offset). */
export function toStateAtParam(date: Date): string {
  return date.toISOString().replace("Z", "+00:00");
}

// ---------------------------------------------------------------------------
// Behavioral signals (11C) — GET /transactions/{id}/behavioral-signals
// ---------------------------------------------------------------------------

export type SignalLevel = "LOW" | "MEDIUM" | "HIGH" | "UNKNOWN";

export interface BehavioralSignal {
  code: string;
  label: string;
  value: number | string | null;
  unit: string | null;
  level: SignalLevel;
  description: string;
  window: string;
  basis: string;
}

export interface BehavioralReport {
  transaction_id: string;
  signals: BehavioralSignal[];
  summary: {
    high_count: number;
    medium_count: number;
    unknown_count: number;
    overall: SignalLevel;
  };
  computed_at: string;
  feature_version: string;
}

export const behavioralReportSchema = z.object({
  transaction_id: z.string(),
  signals: z.array(
    z.object({
      code: z.string(),
      label: z.string(),
      value: z.union([z.number(), z.string()]).nullable(),
      unit: z.string().nullable(),
      level: z.enum(["LOW", "MEDIUM", "HIGH", "UNKNOWN"]),
      description: z.string(),
      window: z.string(),
      basis: z.string(),
    })
  ),
  summary: z.object({
    high_count: z.number(),
    medium_count: z.number(),
    unknown_count: z.number(),
    overall: z.enum(["LOW", "MEDIUM", "HIGH", "UNKNOWN"]),
  }),
  computed_at: z.string(),
  feature_version: z.string(),
});

// ---------------------------------------------------------------------------
// Relationships (11D) — GET /transactions/{id}/relationships
// ---------------------------------------------------------------------------

export interface RelationshipSignal {
  code: string;
  label: string;
  description: string;
  level: SignalLevel;
  count: number | null;
  evidence: string[];
}

export interface RelationshipReport {
  transaction_id: string;
  entities: { type: string; id: string }[];
  edges: {
    from_type: string;
    from_id: string;
    to_type: string;
    to_id: string;
    relation: string;
  }[];
  signals: RelationshipSignal[];
  computed_at: string;
  feature_version: string;
}

export interface RelationshipResponse {
  report: RelationshipReport;
}

const relationshipReportSchema = z.object({
  transaction_id: z.string(),
  entities: z.array(z.object({ type: z.string(), id: z.string() })),
  edges: z.array(
    z.object({
      from_type: z.string(),
      from_id: z.string(),
      to_type: z.string(),
      to_id: z.string(),
      relation: z.string(),
    })
  ),
  signals: z.array(
    z.object({
      code: z.string(),
      label: z.string(),
      description: z.string(),
      level: z.enum(["LOW", "MEDIUM", "HIGH", "UNKNOWN"]),
      count: z.number().nullable(),
      evidence: z.array(z.string()),
    })
  ),
  computed_at: z.string(),
  feature_version: z.string(),
});

export const relationshipResponseSchema = z.object({
  report: relationshipReportSchema,
});

// ---------------------------------------------------------------------------
// Policy simulator (11E) — POST /policy-simulator/run
// ---------------------------------------------------------------------------

export type PolicyVersion =
  | "autonomous-v1"
  | "manual-only-baseline"
  | "autonomous-v2-experimental";

export interface SimulatedDecision {
  transaction_id: string;
  action: string;
  blocked_reason: string | null;
  eligible: boolean;
  gate_allowed: boolean;
  gate_blocked_reason: string | null;
  gate_vetoed: boolean;
}

export interface PolicySimulationResult {
  policy_version: string;
  description: string;
  experimental: boolean;
  transactions_evaluated: number;
  would_release: number;
  would_block: number;
  would_manual_review: number;
  would_no_action: number;
  gate_vetoes: number;
  false_recovery: number | null;
  missed_recovery: number | null;
  provider_calls_avoided: number;
  decision_latency_ms_avg: number;
  decisions: SimulatedDecision[];
}

export interface PolicySimulationRun {
  run_id: string;
  simulated: true;
  affects_live_policy: false;
  ground_truth_available: boolean;
  dataset: {
    source: string;
    transaction_ids: string[];
    dataset_fingerprint: string;
  };
  policies: PolicySimulationResult[];
  comparison: { metric: string; per_policy: Record<string, unknown> }[];
  generated_at: string;
  code_versions: Record<string, unknown>;
}

const simulatedDecisionSchema = z.object({
  transaction_id: z.string(),
  action: z.string(),
  blocked_reason: z.string().nullable(),
  eligible: z.boolean(),
  gate_allowed: z.boolean(),
  gate_blocked_reason: z.string().nullable(),
  gate_vetoed: z.boolean(),
});

const policySimulationResultSchema = z.object({
  policy_version: z.string(),
  description: z.string(),
  experimental: z.boolean(),
  transactions_evaluated: z.number(),
  would_release: z.number(),
  would_block: z.number(),
  would_manual_review: z.number(),
  would_no_action: z.number(),
  gate_vetoes: z.number(),
  false_recovery: z.number().nullable(),
  missed_recovery: z.number().nullable(),
  provider_calls_avoided: z.number(),
  decision_latency_ms_avg: z.number(),
  decisions: z.array(simulatedDecisionSchema),
});

export const policySimulationRunSchema = z.object({
  run_id: z.string(),
  simulated: z.literal(true),
  affects_live_policy: z.literal(false),
  ground_truth_available: z.boolean(),
  dataset: z.object({
    source: z.string(),
    transaction_ids: z.array(z.string()),
    dataset_fingerprint: z.string(),
  }),
  policies: z.array(policySimulationResultSchema),
  comparison: z.array(
    z.object({ metric: z.string(), per_policy: z.record(z.unknown()) })
  ),
  generated_at: z.string(),
  code_versions: z.record(z.unknown()),
});

export interface SimulatorRunRequest {
  policies: PolicyVersion[];
  source: { demo: true } | { demo: false; transaction_ids: string[] };
}

// ---------------------------------------------------------------------------
// Chaos lab (11F) — GET /chaos/scenarios, POST /chaos/run
// ---------------------------------------------------------------------------

export interface ChaosInvariant {
  name: string;
  held: boolean;
  detail: string;
}

export interface ChaosRunResult {
  scenario: string;
  transaction_id: string | null;
  verdict: "PASS" | "FAIL" | "SKIP";
  outcome: {
    decision: string;
    status: string;
    blocked_reason: string | null;
    recovery_id: string | null;
    provider_reference: string | null;
  } | null;
  invariants: ChaosInvariant[];
  note: string | null;
}

export interface ChaosScenario {
  scenario: string;
  description: string;
  expected: Record<string, unknown>;
}

export interface ChaosScenarioListResponse {
  scenarios: ChaosScenario[];
}

export const chaosRunResultSchema = z.object({
  scenario: z.string(),
  transaction_id: z.string().nullable(),
  verdict: z.enum(["PASS", "FAIL", "SKIP"]),
  outcome: z
    .object({
      decision: z.string(),
      status: z.string(),
      blocked_reason: z.string().nullable(),
      recovery_id: z.string().nullable(),
      provider_reference: z.string().nullable(),
    })
    .nullable(),
  invariants: z.array(
    z.object({
      name: z.string(),
      held: z.boolean(),
      detail: z.string(),
    })
  ),
  note: z.string().nullable(),
});

export const chaosScenarioListSchema = z.object({
  scenarios: z.array(
    z.object({
      scenario: z.string(),
      description: z.string(),
      expected: z.record(z.unknown()),
    })
  ),
});

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

/** GET /transactions/{id}/state-at — staff-only temporal query. */
export async function getStateAt(
  transactionId: string,
  timestampParam: string
): Promise<TemporalStateReport> {
  const data = await get<unknown>(
    `/transactions/${encodeURIComponent(transactionId)}/state-at?timestamp=${encodeURIComponent(timestampParam)}`
  );
  return temporalStateReportSchema.parse(data);
}

/** GET /transactions/{id}/behavioral-signals — staff-only. */
export async function getBehavioralSignals(
  transactionId: string
): Promise<BehavioralReport> {
  const data = await get<unknown>(
    `/transactions/${encodeURIComponent(transactionId)}/behavioral-signals`
  );
  return behavioralReportSchema.parse(data);
}

/** GET /transactions/{id}/relationships — staff-only. */
export async function getRelationships(
  transactionId: string
): Promise<RelationshipResponse> {
  const data = await get<unknown>(
    `/transactions/${encodeURIComponent(transactionId)}/relationships`
  );
  return relationshipResponseSchema.parse(data);
}

/** POST /policy-simulator/run — SYSTEM/ADMIN; never affects the live policy. */
export async function runPolicySimulation(
  request: SimulatorRunRequest
): Promise<PolicySimulationRun> {
  const data = await post<unknown>("/policy-simulator/run", request);
  return policySimulationRunSchema.parse(data);
}

/** GET /chaos/scenarios — staff-only catalog. */
export async function getChaosScenarios(): Promise<ChaosScenarioListResponse> {
  const data = await get<unknown>("/chaos/scenarios");
  return chaosScenarioListSchema.parse(data);
}

/** POST /chaos/run — SYSTEM/ADMIN; runs the real engine, asserts invariants. */
export async function runChaosScenario(scenario: string): Promise<ChaosRunResult> {
  const data = await post<unknown>("/chaos/run", { scenario });
  return chaosRunResultSchema.parse(data);
}

/** Human labels for chaos verdicts. */
export function humanizeVerdict(verdict: "PASS" | "FAIL" | "SKIP"): string {
  if (verdict === "PASS") return "Pass";
  if (verdict === "FAIL") return "Fail";
  return "Skipped";
}
