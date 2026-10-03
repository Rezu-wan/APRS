// Stage 10 — Demo control API (contract written by the integrator; do not
// rename exports). All demo endpoints are STAFF-facing against the REAL
// backend engine: preparing a scenario creates controlled data (transaction +
// payment events + risk assessment) through the same services as the public
// API, but NEVER runs recovery — the demo panel always drives the actual
// /recovery/process endpoint. SANDBOX ONLY — no real money moves.
import { z } from "zod";
import { get, post } from "./client";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type DemoScenarioKey = "S1" | "S2" | "S3" | "S4" | "S5" | "S6";

/** Live status of one demo scenario, read from real DB state. */
export interface DemoScenarioStatus {
  key: DemoScenarioKey;
  transaction_id: string;
  title: string;
  subtitle: string;
  story: string;
  /** Pinned expected outcome (catalog metadata, not a live read). */
  expected: {
    anomaly: string;
    risk_level: string;
    decision: string;
    status: string;
  };
  exists: boolean;
  current_state: string | null;
  /** Payment-event evidence ingested. */
  prepared: boolean;
  /** A recovery row exists (processed or blocked). */
  processed: boolean;
  recovery: {
    decision: string;
    status: string;
    recovery_id: string;
    blocked_reason: string | null;
    provider_reference: string | null;
    released_amount: number | null;
    currency: string;
  } | null;
  risk: { anomaly_type: string; risk_level: string } | null;
}

export interface DemoScenariosResponse {
  simulated: boolean;
  scenarios: DemoScenarioStatus[];
}

export interface DemoStatusResponse {
  database: "connected" | "unavailable";
  ml_models: "loaded" | "not_loaded";
  genai: {
    provider: string;
    status: "available" | "fallback" | "unavailable";
    prompt_version: string;
  };
  sandbox_provider: {
    provider: string;
    initial_limit: number;
    available_limit: number;
    currency: string;
    held_entries: number;
    released_entries: number;
  };
}

export interface SandboxLedgerEntry {
  transaction_id: string;
  held_amount: number;
  released_amount: number;
  currency: string;
  provider_reference: string | null;
  status: string | null;
}

export interface SandboxLedgerResponse {
  simulated: boolean;
  initial_limit: number;
  available_limit: number;
  currency: string;
  entries: SandboxLedgerEntry[];
}

export interface DemoResetResponse {
  reset: boolean;
  simulated: boolean;
  scenarios: DemoScenarioStatus[];
}

export interface DemoPrepareResponse {
  prepared: boolean;
  simulated: boolean;
  scenario: DemoScenarioStatus;
  actions: string[];
}

// ---------------------------------------------------------------------------
// Zod schemas — runtime validation, mirroring the backend response models.
// ---------------------------------------------------------------------------

const scenarioStatusSchema = z.object({
  key: z.enum(["S1", "S2", "S3", "S4", "S5", "S6"]),
  transaction_id: z.string(),
  title: z.string(),
  subtitle: z.string(),
  story: z.string(),
  expected: z.object({
    anomaly: z.string(),
    risk_level: z.string(),
    decision: z.string(),
    status: z.string(),
  }),
  exists: z.boolean(),
  current_state: z.string().nullable(),
  prepared: z.boolean(),
  processed: z.boolean(),
  recovery: z
    .object({
      decision: z.string(),
      status: z.string(),
      recovery_id: z.string(),
      blocked_reason: z.string().nullable(),
      provider_reference: z.string().nullable(),
      released_amount: z.number().nullable(),
      currency: z.string(),
    })
    .nullable(),
  risk: z
    .object({ anomaly_type: z.string(), risk_level: z.string() })
    .nullable(),
});

export const demoScenariosResponseSchema = z.object({
  simulated: z.literal(true),
  scenarios: z.array(scenarioStatusSchema),
});

export const demoStatusResponseSchema = z.object({
  database: z.enum(["connected", "unavailable"]),
  ml_models: z.enum(["loaded", "not_loaded"]),
  genai: z.object({
    provider: z.string(),
    status: z.enum(["available", "fallback", "unavailable"]),
    prompt_version: z.string(),
  }),
  sandbox_provider: z.object({
    provider: z.string(),
    initial_limit: z.number(),
    available_limit: z.number(),
    currency: z.string(),
    held_entries: z.number(),
    released_entries: z.number(),
  }),
});

const sandboxLedgerEntrySchema = z.object({
  transaction_id: z.string(),
  held_amount: z.number(),
  released_amount: z.number(),
  currency: z.string(),
  provider_reference: z.string().nullable(),
  status: z.string().nullable(),
});

export const sandboxLedgerResponseSchema = z.object({
  simulated: z.literal(true),
  initial_limit: z.number(),
  available_limit: z.number(),
  currency: z.string(),
  entries: z.array(sandboxLedgerEntrySchema),
});

export const demoResetResponseSchema = z.object({
  reset: z.literal(true),
  simulated: z.literal(true),
  scenarios: z.array(scenarioStatusSchema),
});

export const demoPrepareResponseSchema = z.object({
  prepared: z.literal(true),
  simulated: z.literal(true),
  scenario: scenarioStatusSchema,
  actions: z.array(z.string()),
});

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

/** GET /demo/scenarios — SYSTEM/ADMIN/SUPPORT. */
export async function getDemoScenarios(): Promise<DemoScenariosResponse> {
  const data = await get<unknown>("/demo/scenarios");
  return demoScenariosResponseSchema.parse(data);
}

/** GET /demo/status — SYSTEM/ADMIN/SUPPORT. Console health block. */
export async function getDemoStatus(): Promise<DemoStatusResponse> {
  const data = await get<unknown>("/demo/status");
  return demoStatusResponseSchema.parse(data);
}

/** GET /sandbox/ledger — SYSTEM/ADMIN/SUPPORT. Simulated account state. */
export async function getSandboxLedger(): Promise<SandboxLedgerResponse> {
  const data = await get<unknown>("/sandbox/ledger");
  return sandboxLedgerResponseSchema.parse(data);
}

/** POST /demo/scenarios/{key}/prepare — SYSTEM/ADMIN. Idempotent; never runs recovery. */
export async function prepareScenario(key: DemoScenarioKey): Promise<DemoPrepareResponse> {
  const data = await post<unknown>(`/demo/scenarios/${encodeURIComponent(key)}/prepare`, {});
  return demoPrepareResponseSchema.parse(data);
}

/** POST /demo/scenarios/{key}/inject-late-settlement — SYSTEM/ADMIN, S5 only. */
export async function injectLateSettlement(key: DemoScenarioKey): Promise<DemoPrepareResponse> {
  const data = await post<unknown>(
    `/demo/scenarios/${encodeURIComponent(key)}/inject-late-settlement`,
    {}
  );
  return demoPrepareResponseSchema.parse(data);
}

/** POST /demo/reset — SYSTEM/ADMIN. Purges DEMO-S* rows, resets the sandbox ledger, re-prepares S1–S6. */
export async function resetDemo(): Promise<DemoResetResponse> {
  const data = await post<unknown>("/demo/reset", {});
  return demoResetResponseSchema.parse(data);
}

/** Human labels for blocked reasons (§11 — never a bare "Recovery failed"). */
const BLOCKED_REASON_LABELS: Record<string, string> = {
  DOUBLE_DEDUCTION:
    "Multiple debit evidence detected — releasing funds could double the customer's refund.",
  ALREADY_SUCCESS:
    "A successful settlement was found — there is nothing to recover.",
  INSUFFICIENT_EVIDENCE:
    "Not enough payment evidence to decide — uncertainty never authorizes recovery.",
  NEW_SUCCESSFUL_SETTLEMENT:
    "A successful settlement was detected after the initial assessment.",
  ALREADY_RECOVERED: "This transaction was already recovered — idempotency prevented a second release.",
};

export function humanizeBlockedReason(reason: string | null | undefined): string {
  if (!reason) return "Unknown block reason";
  return BLOCKED_REASON_LABELS[reason] ?? reason;
}
