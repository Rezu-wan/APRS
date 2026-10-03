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
  /** Stage 9: bound customer identity for CUSTOMER keys; null/absent for staff. */
  customer_id?: string | null;
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
  /** descriptive payment attributes (dataset-loaded; absent on old rows) */
  transaction_type?: string | null;
  channel?: string | null;
  direction?: string | null;
  country?: string | null;
  /** merchant-catalog resolution (null when the id has no catalog row) */
  merchant_name?: string | null;
  merchant_category?: string | null;
  failure_prediction: string | null;
  failure_probability: number | null;
  risk_score: number | null;
  safe_to_release_probability: number | null;
  safe_to_release: boolean | null;
  created_at: string;
  updated_at: string;
}

export interface CustomerProfile {
  customer_id: string;
  full_name: string;
  email: string;
  phone: string;
  country: string;
  status: string;
  segment: string;
  risk_profile: string;
  archetype: string | null;
  member_since: string;
}

export interface TransactionListResponse {
  items: Transaction[];
  total: number;
  limit: number;
  offset: number;
}

/** GET /transactions/summary — role-scoped aggregates (customer dashboard). */
export interface TransactionsSummary {
  total: number;
  by_state: Record<string, number>;
  amounts_by_currency: Record<string, string>;
}

// ---------------------------------------------------------------------------
// Customer problem reports (Stage: customer-reports) — evidence only.
// ---------------------------------------------------------------------------

export type ProblemType =
  | "DOUBLE_CHARGED"
  | "PAYMENT_FAILED"
  | "MONEY_NOT_RECEIVED"
  | "UNAUTHORIZED"
  | "OTHER";

export type ProblemStage =
  | "CARD_DEBIT"
  | "GATEWAY"
  | "MERCHANT_CONFIRMATION"
  | "SETTLEMENT"
  | "NOT_SURE";

export type ReportStatus = "OPEN" | "UNDER_REVIEW" | "RESOLVED" | "REJECTED";

export interface CustomerReport {
  report_id: string;
  transaction_id: string;
  customer_id: string;
  problem_type: string;
  stage: string;
  description: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface CustomerReportFileResponse {
  report: CustomerReport;
  already_reported: boolean;
  digital_twin_event_recorded: boolean;
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
  autonomous_recovery?: AutonomousRecoveryStats;
}

// ---------------------------------------------------------------------------
// Zod schemas — runtime validation of API responses where practical.
// ---------------------------------------------------------------------------

export const roleSchema = z.enum(["SYSTEM", "ADMIN", "SUPPORT", "CUSTOMER"]);

export const meResponseSchema = z.object({
  role: roleSchema,
  key_name: z.string(),
  customer_id: z.string().nullable().optional(),
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
  transaction_type: z.string().nullable().optional(),
  channel: z.string().nullable().optional(),
  direction: z.string().nullable().optional(),
  country: z.string().nullable().optional(),
  merchant_name: z.string().nullable().optional(),
  merchant_category: z.string().nullable().optional(),
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

export const transactionListResponseSchema = z.object({
  items: z.array(transactionSchema),
  total: z.number(),
  limit: z.number(),
  offset: z.number(),
});

export const transactionsSummarySchema = z.object({
  total: z.number(),
  by_state: z.record(z.number()),
  amounts_by_currency: z.record(z.string()),
});

export const problemTypeSchema = z.enum([
  "DOUBLE_CHARGED",
  "PAYMENT_FAILED",
  "MONEY_NOT_RECEIVED",
  "UNAUTHORIZED",
  "OTHER",
]);

export const problemStageSchema = z.enum([
  "CARD_DEBIT",
  "GATEWAY",
  "MERCHANT_CONFIRMATION",
  "SETTLEMENT",
  "NOT_SURE",
]);

export const customerReportSchema = z.object({
  report_id: z.string(),
  transaction_id: z.string(),
  customer_id: z.string(),
  problem_type: z.string(),
  stage: z.string(),
  description: z.string(),
  status: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
});

export const customerReportFileResponseSchema = z.object({
  report: customerReportSchema,
  already_reported: z.boolean(),
  digital_twin_event_recorded: z.boolean(),
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

// Additive Stage 8 block — declared before statsSummarySchema uses it.
export const autonomousRecoveryStatsSchema = z.object({
  attempts: z.number(),
  completed: z.number(),
  blocked: z.number(),
  failed: z.number(),
  verified: z.number(),
});

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
  // Additive Stage 8 block — optional so older backends still parse.
  autonomous_recovery: autonomousRecoveryStatsSchema.optional(),
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
  // Dataset-loaded evidence carries no provenance (CSV has no source column);
  // live-pipeline evidence always sets it.
  source: string | null;
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
  source: z.string().nullable(),
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

// ---------------------------------------------------------------------------
// Stage 8 — autonomous recovery (simulated sandbox)
// ---------------------------------------------------------------------------

export type RecoveryStatus =
  | "PENDING"
  | "EXECUTING"
  | "COMPLETED"
  | "FAILED"
  | "BLOCKED"
  | "VERIFICATION_PENDING"
  | "VERIFIED";

export type RecoveryDecision =
  | "AUTO_RECOVERED"
  | "RECOVERY_BLOCKED"
  | "MANUAL_REVIEW_QUEUED"
  | "ALREADY_RECOVERED";

export type RecoveryAction = "RELEASE_LIMIT" | "NO_ACTION" | "MANUAL_REVIEW";

/** Embedded assessment summary shared by the process/evaluate endpoints. */
export interface RecoveryAssessmentSummary {
  anomaly_type: AnomalyType;
  risk_level: RiskLevel;
  recovery_candidate: boolean;
}

export interface RecoveryOutcome {
  transaction_id: string;
  decision: RecoveryDecision;
  action: RecoveryAction;
  status: RecoveryStatus;
  recovery_id: string;
  provider_reference: string | null;
  reason: string;
  simulated: boolean;
  assessment: RecoveryAssessmentSummary;
  digital_twin_recorded: boolean;
}

export interface RecoveryEvaluateResult {
  transaction_id: string;
  eligible: boolean;
  action: RecoveryAction | null;
  decision_reason: string;
  blocked_reason: string | null;
  policy_version: string;
  simulated: boolean;
  assessment: RecoveryAssessmentSummary;
}

/** Server correlation id (Stage 9) — also attached to ApiError instances. */
export type RequestId = string;

/** GET …/recovery — 404 maps to null in the API layer when no recovery exists. */
export interface RecoveryRecord {
  transaction_id: string;
  action: RecoveryAction;
  status: RecoveryStatus;
  recovery_id: string;
  decision_reason: string | null;
  blocked_reason: string | null;
  failure_reason: string | null;
  requested_amount: string;
  released_amount: string | null;
  currency: string;
  /** Null when the provider was never called (blocked/pending rows). */
  provider: string | null;
  provider_reference: string | null;
  policy_version: string;
  created_at: string;
  verified_at: string | null;
  simulated: boolean;
  // Additive Stage 9 auditability block — absent from older backends.
  idempotency_key?: string | null;
  risk_assessment_id?: string | null;
  executor_version?: string | null;
  verifier_version?: string | null;
}

export interface AutonomousRecoveryStats {
  attempts: number;
  completed: number;
  blocked: number;
  failed: number;
  verified: number;
}

export interface StatsSummaryWithRecovery extends StatsSummary {
  autonomous_recovery?: AutonomousRecoveryStats;
}

// Stage 8 (additive): present when the backend reports sandbox recovery stats.
// Declared on StatsSummary itself so pages read it without a narrowing cast.

const recoveryStatusSchema = z.enum([
  "PENDING",
  "EXECUTING",
  "COMPLETED",
  "FAILED",
  "BLOCKED",
  "VERIFICATION_PENDING",
  "VERIFIED",
]);

const recoveryDecisionSchema = z.enum([
  "AUTO_RECOVERED",
  "RECOVERY_BLOCKED",
  "MANUAL_REVIEW_QUEUED",
  "ALREADY_RECOVERED",
]);

const recoveryActionSchema = z.enum(["RELEASE_LIMIT", "NO_ACTION", "MANUAL_REVIEW"]);

const recoveryAssessmentSummarySchema = z.object({
  anomaly_type: riskAssessmentSchema.shape.anomaly_type,
  risk_level: riskAssessmentSchema.shape.risk_level,
  recovery_candidate: z.boolean(),
});

export const recoveryOutcomeSchema = z.object({
  transaction_id: z.string(),
  decision: recoveryDecisionSchema,
  action: recoveryActionSchema,
  status: recoveryStatusSchema,
  recovery_id: z.string(),
  provider_reference: z.string().nullable(),
  reason: z.string(),
  simulated: z.literal(true),
  assessment: recoveryAssessmentSummarySchema,
  digital_twin_recorded: z.boolean(),
});

export const recoveryEvaluateResultSchema = z.object({
  transaction_id: z.string(),
  eligible: z.boolean(),
  action: recoveryActionSchema.nullable(),
  decision_reason: z.string(),
  blocked_reason: z.string().nullable(),
  policy_version: z.string(),
  simulated: z.literal(true),
  assessment: recoveryAssessmentSummarySchema,
});

// The GET …/recovery endpoint serializes amounts as NUMBERS (float, from the
// Numeric column) and provider is null whenever the provider was never
// called (blocked/pending rows). Amounts are normalized to strings here so
// every consumer keeps working with the display formatters.
const amountLike = z.union([z.string(), z.number()]).transform(String);

export const recoveryRecordSchema = z.object({
  transaction_id: z.string(),
  action: recoveryActionSchema,
  status: recoveryStatusSchema,
  recovery_id: z.string(),
  decision_reason: z.string().nullable(),
  blocked_reason: z.string().nullable(),
  failure_reason: z.string().nullable(),
  requested_amount: amountLike,
  released_amount: amountLike.nullable(),
  currency: z.string(),
  provider: z.string().nullable(),
  provider_reference: z.string().nullable(),
  policy_version: z.string(),
  created_at: z.string(),
  verified_at: z.string().nullable(),
  simulated: z.literal(true),
  // Additive Stage 9 auditability block — optional so older backends still parse.
  idempotency_key: z.string().nullable().optional(),
  risk_assessment_id: z.string().nullable().optional(),
  executor_version: z.string().nullable().optional(),
  verifier_version: z.string().nullable().optional(),
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

const RECOVERY_STATUS_LABELS: Record<RecoveryStatus, string> = {
  VERIFIED: "Verified",
  BLOCKED: "Blocked",
  FAILED: "Failed",
  PENDING: "Pending",
  EXECUTING: "Executing",
  COMPLETED: "Completed",
  VERIFICATION_PENDING: "Verifying",
};

export function humanizeRecoveryStatus(status: RecoveryStatus | string): string {
  return RECOVERY_STATUS_LABELS[status as RecoveryStatus] ?? status;
}

const RECOVERY_DECISION_LABELS: Record<RecoveryDecision, string> = {
  AUTO_RECOVERED: "Automatically recovered",
  RECOVERY_BLOCKED: "Recovery blocked",
  MANUAL_REVIEW_QUEUED: "Queued for manual review",
  ALREADY_RECOVERED: "Already recovered",
};

export function humanizeRecoveryDecision(decision: RecoveryDecision | string): string {
  return RECOVERY_DECISION_LABELS[decision as RecoveryDecision] ?? decision;
}

const RECOVERY_ACTION_LABELS: Record<RecoveryAction, string> = {
  RELEASE_LIMIT: "Release limit",
  NO_ACTION: "No automatic recovery",
  MANUAL_REVIEW: "Manual review",
};

export function humanizeRecoveryAction(action: RecoveryAction | string): string {
  return RECOVERY_ACTION_LABELS[action as RecoveryAction] ?? action;
}

const PROBLEM_TYPE_LABELS: Record<ProblemType, string> = {
  DOUBLE_CHARGED: "Charged more than once",
  PAYMENT_FAILED: "Payment failed",
  MONEY_NOT_RECEIVED: "Money not received",
  UNAUTHORIZED: "Transaction not recognised",
  OTHER: "Other problem",
};

export function humanizeProblemType(type: string): string {
  return PROBLEM_TYPE_LABELS[type as ProblemType] ?? type;
}

const PROBLEM_STAGE_LABELS: Record<ProblemStage, string> = {
  CARD_DEBIT: "Bank debit",
  GATEWAY: "Gateway",
  MERCHANT_CONFIRMATION: "Merchant confirmation",
  SETTLEMENT: "Settlement",
  NOT_SURE: "Not sure",
};

export function humanizeProblemStage(stage: string): string {
  return PROBLEM_STAGE_LABELS[stage as ProblemStage] ?? stage;
}

const REPORT_STATUS_LABELS: Record<ReportStatus, string> = {
  OPEN: "Open",
  UNDER_REVIEW: "Under review",
  RESOLVED: "Resolved",
  REJECTED: "Rejected",
};

export function humanizeReportStatus(status: string): string {
  return REPORT_STATUS_LABELS[status as ReportStatus] ?? status;
}

export const customerProfileSchema = z.object({
  customer_id: z.string(),
  full_name: z.string(),
  email: z.string(),
  phone: z.string(),
  country: z.string(),
  status: z.string(),
  segment: z.string(),
  risk_profile: z.string(),
  archetype: z.string().nullable(),
  member_since: z.string(),
});

/** "mobile_app" -> "Mobile app" — channel/type/archetype words arrive
 * snake_cased from the dataset. */
export function humanizeSnakeWord(value: string): string {
  return value
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}
