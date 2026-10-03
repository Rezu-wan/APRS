// Realistic fixture builders shared by all tests.
import type {
  CustomerProfileResponse,
  CustomerSearchResult,
  SupportCase,
  SupportOverviewResponse,
  SupportTransactionSummary,
} from "../api/support";
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
// Stage 12 support workspace
// ---------------------------------------------------------------------------

export function makeSupportTransactionSummary(
  overrides: Partial<SupportTransactionSummary> = {}
): SupportTransactionSummary {
  return {
    transaction_id: "TXN-SUP-1",
    customer_id: "CUST-SUP-1",
    merchant_id: "M-2002",
    amount: 1500,
    currency: "BDT",
    timestamp: iso(30),
    current_state: "RECOVERY_PENDING",
    failure_reason: "GATEWAY_TIMEOUT",
    risk_score: 0.12,
    channel: "mobile_app",
    country: "BD",
    direction: "DEBIT",
    transaction_type: "P2P",
    ...overrides,
  };
}

export function makeSupportCase(
  overrides: Partial<SupportCase> = {}
): SupportCase {
  return {
    case_id: "CASE-000000000001",
    transaction_id: "TXN-SUP-1",
    customer_id: "CUST-SUP-1",
    subject: "Customer reports a failed payment",
    description: "Money left the account but the merchant never confirmed.",
    status: "OPEN",
    priority: "HIGH",
    created_by: "api_key_support",
    assignee: null,
    notes: [],
    created_at: iso(20),
    updated_at: iso(20),
    resolved_at: null,
    ...overrides,
  };
}

export function makeCustomerSearchResult(
  overrides: Partial<CustomerSearchResult> = {}
): CustomerSearchResult {
  return {
    customer_id: "CUST-SUP-1",
    full_name: "Nur Rahim",
    email: "nur.rahim@example.com",
    phone: "+8801700000001",
    status: "active",
    segment: "retail",
    risk_profile: "LOW",
    country: "BD",
    transaction_count: 12,
    failed_transaction_count: 2,
    last_activity_at: iso(30),
    open_case_count: 1,
    dataset_known: true,
    ...overrides,
  };
}

export function makeCustomerProfile(
  overrides: Partial<CustomerProfileResponse> = {}
): CustomerProfileResponse {
  return {
    customer: {
      customer_id: "CUST-SUP-1",
      full_name: "Nur Rahim",
      email: "nur.rahim@example.com",
      phone: "+8801700000001",
      status: "active",
      segment: "retail",
      risk_profile: "LOW",
      country: "BD",
    },
    accounts: [
      {
        account_id: "ACCT-SUP-1",
        account_type: "savings",
        currency: "BDT",
        balance: 42000,
        status: "active",
        is_primary: true,
        last_activity_at: iso(30),
      },
    ],
    aggregate: {
      transaction_count: 12,
      failed_count: 2,
      recovered_count: 1,
      last_activity_at: iso(30),
    },
    recent_transactions: [makeSupportTransactionSummary()],
    behavior_signals: [],
    open_cases: [],
    dataset_known: true,
    ...overrides,
  };
}

export function makeSupportOverview(
  overrides: Partial<SupportOverviewResponse> = {}
): SupportOverviewResponse {
  return {
    cases_by_status: {
      open: 2,
      in_progress: 1,
      waiting_for_customer: 0,
      escalated: 1,
      resolved: 5,
      closed: 9,
    },
    needs_attention: [
      {
        transaction_id: "TXN-BLOCKED-1",
        customer_id: "CUST-SUP-1",
        amount: 1200,
        currency: "BDT",
        timestamp: iso(15),
        failure_reason: "DOUBLE_DEBIT_EVIDENCE",
        recovery_status: "BLOCKED",
        blocked_reason: "DOUBLE_DEDUCTION",
        risk_level: "MEDIUM",
        anomaly_type: "SUSPICIOUS_PATTERN",
        open_case_id: null,
      },
    ],
    recent_activity: [
      {
        transaction_id: "TXN-SUP-1",
        customer_id: "CUST-SUP-1",
        customer_name: "Nur Rahim",
        amount: 1500,
        currency: "BDT",
        timestamp: iso(30),
        current_state: "RECOVERY_PENDING",
        failure_reason: "GATEWAY_TIMEOUT",
      },
    ],
    generated_at: iso(0),
    ...overrides,
  };
}
