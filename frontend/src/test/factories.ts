// Realistic fixture builders shared by all tests.
import type {
  ExplanationResponse,
  PaymentEventOut,
  RecoveryDecisionResponse,
  ReconstructionResult,
  RiskAssessment,
  RiskAssessmentResponse,
  StatsSummary,
  TimelineEvent,
  TimelineResponse,
  Transaction,
  TransactionState,
} from "../types/api";

const iso = (minutesAgo: number): string =>
  new Date(Date.parse("2026-10-02T10:00:00Z") - minutesAgo * 60_000).toISOString();

export function makeTransaction(overrides: Partial<Transaction> = {}): Transaction {
  return {
    transaction_id: "TXN-1",
    user_id: "U-1001",
    merchant_id: "M-2002",
    amount: "1500.00",
    currency: "BDT",
    timestamp: iso(30),
    gateway_latency_ms: 4200,
    retry_count: 2,
    network_quality: "POOR",
    previous_failures: 1,
    account_age_days: 210,
    failure_reason: "GATEWAY_TIMEOUT",
    current_state: "RECOVERY_PENDING",
    failure_prediction: "WILL_RECOVER",
    failure_probability: 0.18,
    risk_score: 0.12,
    safe_to_release_probability: 0.91,
    safe_to_release: true,
    created_at: iso(31),
    updated_at: iso(5),
    ...overrides,
  };
}

export function makeTimelineEvent(
  index: number,
  overrides: Partial<TimelineEvent> = {}
): TimelineEvent {
  return {
    event_id: `EVT-${index}`,
    event_type: "STATE_TRANSITION",
    timestamp: iso((index + 1) * 5),
    previous_state: index === 0 ? null : "INITIATED",
    new_state: "PROCESSING",
    failure_prediction: null,
    risk_score: null,
    safe_to_release_probability: null,
    safe_to_release: null,
    reason: null,
    metadata: null,
    ...overrides,
  };
}

/** Newest-first? No — oldest-first, which is what a timeline reads naturally in. */
export function makeTimeline(count: number, overrides: Partial<TimelineResponse> = {}): TimelineResponse {
  return {
    transaction_id: "TXN-1",
    current_state: "RECOVERY_PENDING",
    event_count: count,
    events: Array.from({ length: count }, (_, i) =>
      makeTimelineEvent(i, {
        new_state: i === 0 ? "INITIATED" : "PROCESSING",
      })
    ),
    ...overrides,
  };
}

export function makeDecision(
  overrides: Partial<RecoveryDecisionResponse> = {}
): RecoveryDecisionResponse {
  return {
    transaction_id: "TXN-1",
    decision: "LIMIT_RELEASED",
    safe_to_release: true,
    safe_to_release_probability: 0.91,
    risk_score: 0.12,
    reason: "Strong repayment history and low risk score.",
    decided_by: "policy:v1",
    decided_at: iso(2),
    current_state: "LIMIT_RELEASED",
    already_applied: false,
    ...overrides,
  };
}

export function makeExplanation(overrides: Partial<ExplanationResponse> = {}): ExplanationResponse {
  return {
    transaction_id: "TXN-1",
    language: "bn",
    audience: "customer",
    explanation: "আপনার লিমিট পুনরায় চালু করা হয়েছে।",
    provider: "openai",
    model: "gpt-4o-mini",
    prompt_version: "v1",
    is_fallback: false,
    cached: false,
    generated_at: iso(1),
    ...overrides,
  };
}

export function makeReconstructionEvent(
  index: number,
  overrides: Partial<PaymentEventOut> = {}
): PaymentEventOut {
  return {
    event_id: `PEVT-${index}`,
    transaction_id: "TXN-1",
    provider_event_id: `PROV-${index}`,
    event_type: "DEBIT_CONFIRMED",
    source: "SIMULATOR",
    status: "CONFIRMED",
    event_timestamp: iso((index + 1) * 2),
    reference_id: null,
    latency_ms: null,
    metadata: null,
    ...overrides,
  };
}

export function makeReconstruction(
  overrides: Partial<ReconstructionResult> = {}
): ReconstructionResult {
  return {
    transaction_id: "TXN-1",
    ordered_events: [
      makeReconstructionEvent(0, { event_type: "BANK_DEBIT_CONFIRMED", status: "CONFIRMED" }),
      makeReconstructionEvent(1, { event_type: "GATEWAY_CONFIRMED", status: "CONFIRMED" }),
      makeReconstructionEvent(2, { event_type: "MERCHANT_CONFIRMATION_TIMEOUT", status: "TIMEOUT" }),
      makeReconstructionEvent(3, { event_type: "SETTLEMENT_NOT_CONFIRMED", status: "NOT_CONFIRMED" }),
    ],
    current_stage: "MERCHANT_CONFIRMATION",
    last_successful_stage: "GATEWAY",
    failure_stage: "MERCHANT_CONFIRMATION",
    root_cause: "MERCHANT_CONFIRMATION_TIMEOUT",
    customer_debit_status: "CONFIRMED",
    gateway_status: "CONFIRMED",
    merchant_confirmation_status: "TIMEOUT",
    settlement_status: "NOT_CONFIRMED",
    reconstruction_confidence: 0.71,
    missing_events: ["SETTLEMENT_CONFIRMED"],
    evidence_summary: [
      "Customer bank debit confirmed after 210 ms.",
      "Gateway authorization confirmed after 4,200 ms.",
      "Merchant confirmation timed out after 30,000 ms.",
      "Settlement was not confirmed by the provider.",
    ],
    reconstructed_at: iso(1),
    digital_twin_event_recorded: true,
    ...overrides,
  };
}

export function makeStats(overrides: Partial<StatsSummary> = {}): StatsSummary {
  return {
    total: 120,
    by_state: {
      INITIATED: 5,
      PROCESSING: 10,
      SUCCESS: 70,
      FAILED: 15,
      STALLED: 5,
      RECOVERY_PENDING: 10,
      LIMIT_RELEASED: 4,
      MANUAL_REVIEW: 1,
    } as Record<TransactionState, number>,
    decisions: {
      LIMIT_RELEASED: 4,
      MANUAL_REVIEW: 1,
      RECOVERY_REJECTED: 0,
    },
    ...overrides,
  };
}

export function makeRiskAssessment(
  overrides: Partial<RiskAssessment> = {}
): RiskAssessment {
  return {
    transaction_id: "TXN-1",
    assessment_id: "ASSESS-1",
    anomaly_type: "GENUINE_FAILURE",
    risk_level: "LOW",
    risk_score: 0.14,
    ml_anomaly_score: 0.09,
    deterministic_risk_score: 0.2,
    recovery_candidate: true,
    recovery_block_reason: null,
    evidence: [
      {
        code: "GATEWAY_TIMEOUT",
        description: "Gateway authorization timed out after 30,000 ms.",
        source: "SIMULATOR",
        severity: "MEDIUM",
      },
      {
        code: "SINGLE_DEBIT",
        description: "Exactly one customer bank debit confirmed.",
        source: "SIMULATOR",
        severity: "LOW",
      },
    ],
    triggered_rules: [{ rule_id: "R1", name: "Genuine failure detected" }],
    reconstruction_root_cause: "GATEWAY_TIMEOUT",
    reconstruction_confidence: 0.8,
    customer_reported_failure: false,
    model_version: "xgb-v3",
    rule_version: "rules-v2",
    created_at: iso(2),
    ...overrides,
  };
}

export function makeRiskAssessmentResponse(
  overrides: Partial<RiskAssessmentResponse> = {},
  assessmentOverrides: Partial<RiskAssessment> = {}
): RiskAssessmentResponse {
  return {
    assessment: makeRiskAssessment(assessmentOverrides),
    reused: false,
    digital_twin_event_recorded: true,
    ...overrides,
  };
}


/** Stage 8: a VERIFIED autonomous recovery record (defaults) — override status
 * to exercise blocked/failed panel states. */
export function makeRecovery(
  overrides: Partial<import("../types/api").RecoveryRecord> = {}
): import("../types/api").RecoveryRecord {
  return {
    transaction_id: "TXN-RECOVERY-1",
    action: "RELEASE_LIMIT",
    status: "VERIFIED",
    recovery_id: "d5f2a1b0-0000-4000-8000-000000000001",
    decision_reason: "Genuine failure with confirmed debit and no settlement.",
    blocked_reason: null,
    failure_reason: null,
    requested_amount: "30.00",
    released_amount: "30.00",
    currency: "BDT",
    provider: "mock",
    provider_reference: "REL-A1B2C3D4",
    policy_version: "autonomous-v1",
    created_at: iso(2),
    verified_at: iso(3),
    simulated: true,
    idempotency_key: "a".repeat(64),
    risk_assessment_id: "ASSESS-1",
    executor_version: "v1",
    verifier_version: "v1",
    ...overrides,
  };
}

export function makeRecoveryOutcome(
  overrides: Partial<import("../types/api").RecoveryOutcome> = {}
): import("../types/api").RecoveryOutcome {
  return {
    transaction_id: "TXN-RECOVERY-1",
    decision: "AUTO_RECOVERED",
    action: "RELEASE_LIMIT",
    status: "VERIFIED",
    recovery_id: "d5f2a1b0-0000-4000-8000-000000000001",
    provider_reference: "REL-A1B2C3D4",
    reason: "Genuine failure with confirmed debit and no settlement.",
    simulated: true,
    assessment: {
      anomaly_type: "GENUINE_FAILURE",
      risk_level: "LOW",
      recovery_candidate: true,
    },
    digital_twin_recorded: true,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Customer dashboard fixtures — role-scoped list/summary + problem reports.
// ---------------------------------------------------------------------------

export function makeTransactionsSummary(
  overrides: Partial<import("../types/api").TransactionsSummary> = {}
): import("../types/api").TransactionsSummary {
  return {
    total: 3,
    by_state: { RECOVERY_PENDING: 2, SUCCESS: 1 },
    amounts_by_currency: { BDT: "4200.00" },
    ...overrides,
  };
}

export function makeTransactionListResponse(
  transactions: Transaction[],
  overrides: Partial<import("../types/api").TransactionListResponse> = {}
): import("../types/api").TransactionListResponse {
  return {
    items: transactions,
    total: transactions.length,
    limit: 10,
    offset: 0,
    ...overrides,
  };
}

export function makeCustomerReport(
  overrides: Partial<import("../types/api").CustomerReport> = {}
): import("../types/api").CustomerReport {
  return {
    report_id: "RPT-1",
    transaction_id: "TXN-1",
    customer_id: "alice",
    problem_type: "PAYMENT_FAILED",
    stage: "GATEWAY",
    description: "The gateway timed out but my card was charged.",
    status: "OPEN",
    created_at: iso(10),
    updated_at: iso(10),
    ...overrides,
  };
}

export function makeCustomerReportFileResponse(
  overrides: Partial<import("../types/api").CustomerReportFileResponse> = {}
): import("../types/api").CustomerReportFileResponse {
  const report = makeCustomerReport();
  return {
    report,
    already_reported: false,
    digital_twin_event_recorded: true,
    ...overrides,
  };
}

export function makeCustomerProfile(
  overrides: Partial<import("../types/api").CustomerProfile> = {}
): import("../types/api").CustomerProfile {
  return {
    customer_id: "CUST-000084",
    full_name: "Sabbir Mustafi",
    email: "sabbir.mustafi241@example.com",
    phone: "+880186585156",
    country: "BD",
    status: "active",
    segment: "retail",
    risk_profile: "LOW",
    archetype: "biller",
    member_since: "2026-02-20T05:57:00Z",
    ...overrides,
  };
}
