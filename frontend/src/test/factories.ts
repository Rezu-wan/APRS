// Realistic fixture builders shared by all tests.
import type {
  ExplanationResponse,
  RecoveryDecisionResponse,
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
