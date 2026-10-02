// PINNED shared contract with the backend — do not rename exports.
import { z } from "zod";

export type Role = "SYSTEM" | "ADMIN" | "SUPPORT" | "CUSTOMER";

export type TransactionState =
  | "INITIATED"
  | "PROCESSING"
  | "SUCCESS"
  | "FAILED"
  | "STALLED"
  | "RISK_ASSESSED"
  | "RECOVERY_PENDING"
  | "LIMIT_RELEASED"
  | "MANUAL_REVIEW"
  | "RECOVERY_REJECTED";

export interface MeResponse {
  role: Role;
  key_name: string;
}

export interface Transaction {
  transaction_id: string;
  user_id: string;
  merchant_id: string;
  amount: string;
  currency: string;
  timestamp: string;
  gateway_latency_ms: number;
  retry_count: number;
  network_quality: string;
  previous_failures: number;
  account_age_days: number;
  failure_reason: string | null;
  current_state: TransactionState;
  failure_prediction: string | null;
  failure_probability: number | null;
  risk_score: number | null;
  safe_to_release_probability: number | null;
  safe_to_release: boolean | null;
  created_at: string;
  updated_at: string;
}

export interface TimelineEvent {
  event_id: string;
  event_type: string;
  timestamp: string;
  previous_state: string | null;
  new_state: string;
  failure_prediction: string | null;
  risk_score: number | null;
  safe_to_release_probability: number | null;
  safe_to_release: boolean | null;
  reason: string | null;
  metadata: Record<string, unknown> | null;
}

export interface TimelineResponse {
  transaction_id: string;
  current_state: string;
  event_count: number;
  events: TimelineEvent[];
}

export interface RecoveryDecisionResponse {
  transaction_id: string;
  decision: "LIMIT_RELEASED" | "MANUAL_REVIEW" | "RECOVERY_REJECTED";
  safe_to_release: boolean;
  safe_to_release_probability: number;
  risk_score: number;
  reason: string;
  decided_by: string;
  decided_at: string;
  current_state: string;
  already_applied: boolean;
}

export type Language = "bn" | "en";
export type Audience = "customer" | "support" | "system";

export interface ExplanationResponse {
  transaction_id: string;
  language: Language;
  audience: Audience;
  explanation: string;
  provider: string;
  model: string;
  prompt_version: string;
  is_fallback: boolean;
  cached: boolean;
  generated_at: string;
}

export interface StatsSummary {
  total: number;
  by_state: Record<string, number>;
  decisions: {
    LIMIT_RELEASED: number;
    MANUAL_REVIEW: number;
    RECOVERY_REJECTED: number;
  };
  /** Additive Stage 7 block — absent from older backend payloads. */
  risk_assessments?: {
    total: number;
    by_anomaly_type: Record<string, number>;
    by_risk_level: Record<string, number>;
    recovery_candidates: number;
  };
}

// ---------------------------------------------------------------------------
// Zod schemas — runtime validation of API responses where practical.
// ---------------------------------------------------------------------------

export const roleSchema = z.enum(["SYSTEM", "ADMIN", "SUPPORT", "CUSTOMER"]);

export const meResponseSchema = z.object({
  role: roleSchema,
  key_name: z.string(),
});

export const transactionStateSchema = z.enum([
  "INITIATED",
  "PROCESSING",
  "SUCCESS",
  "FAILED",
  "STALLED",
  "RISK_ASSESSED",
  "RECOVERY_PENDING",
  "LIMIT_RELEASED",
  "MANUAL_REVIEW",
  "RECOVERY_REJECTED",
]);

const nullableNumber = z.number().nullable();

export const transactionSchema = z.object({
  transaction_id: z.string(),
  user_id: z.string(),
  merchant_id: z.string(),
  amount: z.string(),
  currency: z.string(),
  timestamp: z.string(),
  gateway_latency_ms: z.number(),
  retry_count: z.number(),
  network_quality: z.string(),
  previous_failures: z.number(),
  account_age_days: z.number(),
  failure_reason: z.string().nullable(),
  current_state: transactionStateSchema,
  failure_prediction: z.string().nullable(),
  failure_probability: nullableNumber,
  risk_score: nullableNumber,
  safe_to_release_probability: nullableNumber,
  safe_to_release: z.boolean().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
});

export const timelineEventSchema = z.object({
  event_id: z.string(),
  event_type: z.string(),
  timestamp: z.string(),
  previous_state: z.string().nullable(),
  new_state: z.string(),
  failure_prediction: z.string().nullable(),
  risk_score: nullableNumber,
  safe_to_release_probability: nullableNumber,
  safe_to_release: z.boolean().nullable(),
  reason: z.string().nullable(),
  metadata: z.record(z.unknown()).nullable(),
});

export const timelineResponseSchema = z.object({
  transaction_id: z.string(),
  current_state: z.string(),
  event_count: z.number(),
  events: z.array(timelineEventSchema),
});

export const recoveryDecisionResponseSchema = z.object({
  transaction_id: z.string(),
  decision: z.enum(["LIMIT_RELEASED", "MANUAL_REVIEW", "RECOVERY_REJECTED"]),
  safe_to_release: z.boolean(),
  safe_to_release_probability: z.number(),
  risk_score: z.number(),
  reason: z.string(),
  decided_by: z.string(),
  decided_at: z.string(),
  current_state: z.string(),
  already_applied: z.boolean(),
});

export const explanationResponseSchema = z.object({
  transaction_id: z.string(),
  language: z.enum(["bn", "en"]),
  audience: z.enum(["customer", "support", "system"]),
  explanation: z.string(),
  provider: z.string(),
  model: z.string(),
  prompt_version: z.string(),
  is_fallback: z.boolean(),
  cached: z.boolean(),
  generated_at: z.string(),
});

export type ReconstructionStage =
  | "BANK_DEBIT"
  | "GATEWAY"
  | "MERCHANT_CONFIRMATION"
  | "SETTLEMENT"
  | "UNAVAILABLE";

export type ReconstructionEventStatus =
  | "CONFIRMED"
  | "FAILED"
  | "TIMEOUT"
  | "ERROR"
  | "NOT_CONFIRMED"
  | "OBSERVED"
  | "NOT_OBSERVED";

export interface PaymentEventOut {
  event_id: string;
  transaction_id: string;
  provider_event_id: string;
  event_type: string;
  source: string;
  status: ReconstructionEventStatus;
  event_timestamp: string;
  reference_id: string | null;
  latency_ms: number | null;
  metadata: Record<string, unknown> | null;
}

export interface ReconstructionResult {
  transaction_id: string;
  ordered_events: PaymentEventOut[];
  current_stage: ReconstructionStage;
  last_successful_stage: ReconstructionStage;
  failure_stage: ReconstructionStage;
  root_cause: string;
  customer_debit_status: ReconstructionEventStatus;
  gateway_status: ReconstructionEventStatus;
  merchant_confirmation_status: ReconstructionEventStatus;
  settlement_status: ReconstructionEventStatus;
  reconstruction_confidence: number;
  missing_events: string[];
  evidence_summary: string[];
  reconstructed_at: string;
  digital_twin_event_recorded: boolean;
}

export const statsSummarySchema = z.object({
  total: z.number(),
  by_state: z.record(z.number()),
  decisions: z.object({
    LIMIT_RELEASED: z.number(),
    MANUAL_REVIEW: z.number(),
    RECOVERY_REJECTED: z.number(),
  }),
  // Additive Stage 7 block — optional so older backends still parse.
  risk_assessments: z
    .object({
      total: z.number(),
      by_anomaly_type: z.record(z.number()),
      by_risk_level: z.record(z.number()),
      recovery_candidates: z.number(),
    })
    .optional(),
});

export const paymentEventOutSchema = z.object({
  event_id: z.string(),
  transaction_id: z.string(),
  provider_event_id: z.string(),
  event_type: z.string(),
  source: z.string(),
  status: z.enum([
    "CONFIRMED",
    "FAILED",
    "TIMEOUT",
    "ERROR",
    "NOT_CONFIRMED",
    "OBSERVED",
    "NOT_OBSERVED",
  ]),
  event_timestamp: z.string(),
  reference_id: z.string().nullable(),
  latency_ms: nullableNumber,
  metadata: z.record(z.unknown()).nullable(),
});

export const reconstructionStageSchema = z.enum([
  "BANK_DEBIT",
  "GATEWAY",
  "MERCHANT_CONFIRMATION",
  "SETTLEMENT",
  "UNAVAILABLE",
]);

// ---------------------------------------------------------------------------
// Stage 7 — hybrid risk & anomaly classification
// ---------------------------------------------------------------------------

export type AnomalyType =
  | "NONE"
  | "GENUINE_FAILURE"
  | "DOUBLE_DEDUCTION"
  | "DUPLICATE_TRANSACTION"
  | "SUCCESSFUL_BUT_UNCONFIRMED"
  | "FALSE_COMPLAINT"
  | "SUSPICIOUS"
  | "INCOMPLETE"
  | "UNKNOWN";

export type RiskLevel = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" | "UNKNOWN";

export interface EvidenceItem {
  code: string;
  description: string;
  source: string;
  severity: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
}

export interface TriggeredRule {
  rule_id: string;
  name: string;
}

export interface RiskAssessment {
  transaction_id: string;
  assessment_id: string;
  anomaly_type: AnomalyType;
  risk_level: RiskLevel;
  risk_score: number;
  ml_anomaly_score: number | null;
  deterministic_risk_score: number;
  recovery_candidate: boolean;
  recovery_block_reason: string | null;
  evidence: EvidenceItem[];
  triggered_rules: TriggeredRule[];
  reconstruction_root_cause: string | null;
  reconstruction_confidence: number | null;
  customer_reported_failure: boolean;
  model_version: string;
  rule_version: string;
  created_at: string;
}

export interface RiskAssessmentResponse {
  assessment: RiskAssessment;
  reused: boolean;
  digital_twin_event_recorded: boolean;
}

const severitySchema = z.enum(["LOW", "MEDIUM", "HIGH", "CRITICAL"]);

export const evidenceItemSchema = z.object({
  code: z.string(),
  description: z.string(),
  source: z.string(),
  severity: severitySchema,
});

export const triggeredRuleSchema = z.object({
  rule_id: z.string(),
  name: z.string(),
});

export const riskAssessmentSchema = z.object({
  transaction_id: z.string(),
  assessment_id: z.string(),
  anomaly_type: z.enum([
    "NONE",
    "GENUINE_FAILURE",
    "DOUBLE_DEDUCTION",
    "DUPLICATE_TRANSACTION",
    "SUCCESSFUL_BUT_UNCONFIRMED",
    "FALSE_COMPLAINT",
    "SUSPICIOUS",
    "INCOMPLETE",
    "UNKNOWN",
  ]),
  risk_level: z.enum(["LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"]),
  risk_score: z.number(),
  ml_anomaly_score: nullableNumber,
  deterministic_risk_score: z.number(),
  recovery_candidate: z.boolean(),
  recovery_block_reason: z.string().nullable(),
  evidence: z.array(evidenceItemSchema),
  triggered_rules: z.array(triggeredRuleSchema),
  reconstruction_root_cause: z.string().nullable(),
  reconstruction_confidence: nullableNumber,
  customer_reported_failure: z.boolean(),
  model_version: z.string(),
  rule_version: z.string(),
  created_at: z.string(),
});

export const riskAssessmentResponseSchema = z.object({
  assessment: riskAssessmentSchema,
  reused: z.boolean(),
  digital_twin_event_recorded: z.boolean(),
});

/**
 * Display-only humanizers — softer wording for sensitive classifications
 * (FALSE_COMPLAINT deliberately never uses the word "fraud").
 */
const ANOMALY_LABELS: Record<AnomalyType, string> = {
  NONE: "No anomaly",
  GENUINE_FAILURE: "Genuine failure",
  DOUBLE_DEDUCTION: "Double deduction",
  DUPLICATE_TRANSACTION: "Duplicate transaction",
  SUCCESSFUL_BUT_UNCONFIRMED: "Successful but unconfirmed",
  FALSE_COMPLAINT: "Possible false complaint",
  SUSPICIOUS: "Suspicious — flagged for review",
  INCOMPLETE: "Incomplete evidence",
  UNKNOWN: "Unknown",
};

export function humanizeAnomaly(anomaly: AnomalyType | string): string {
  return ANOMALY_LABELS[anomaly as AnomalyType] ?? anomaly;
}

const RISK_LEVEL_LABELS: Record<RiskLevel, string> = {
  LOW: "Low",
  MEDIUM: "Medium",
  HIGH: "High",
  CRITICAL: "Critical",
  UNKNOWN: "Unknown",
};

export function humanizeRiskLevel(level: RiskLevel | string): string {
  return RISK_LEVEL_LABELS[level as RiskLevel] ?? level;
}

export const reconstructionResultSchema = z.object({
  transaction_id: z.string(),
  ordered_events: z.array(paymentEventOutSchema),
  current_stage: reconstructionStageSchema,
  last_successful_stage: reconstructionStageSchema,
  failure_stage: reconstructionStageSchema,
  root_cause: z.string(),
  customer_debit_status: paymentEventOutSchema.shape.status,
  gateway_status: paymentEventOutSchema.shape.status,
  merchant_confirmation_status: paymentEventOutSchema.shape.status,
  settlement_status: paymentEventOutSchema.shape.status,
  reconstruction_confidence: z.number(),
  missing_events: z.array(z.string()),
  evidence_summary: z.array(z.string()),
  reconstructed_at: z.string(),
  digital_twin_event_recorded: z.boolean(),
});
