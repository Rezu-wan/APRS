import { ApiError, get, post } from "./client";
import {
  reconstructionResultSchema,
  recoveryEvaluateResultSchema,
  recoveryOutcomeSchema,
  recoveryRecordSchema,
  riskAssessmentResponseSchema,
  statsSummarySchema,
  timelineResponseSchema,
  transactionListResponseSchema,
  transactionSchema,
  transactionsSummarySchema,
  type RecoveryEvaluateResult,
  type RecoveryOutcome,
  type RecoveryRecord,
  type ReconstructionResult,
  type RiskAssessmentResponse,
  type StatsSummary,
  type TimelineResponse,
  type Transaction,
  type TransactionListResponse,
  type TransactionsSummary,
} from "../types/api";

/** GET /transactions/{id} — 404 propagates as ApiError. */
export async function getTransaction(id: string): Promise<Transaction> {
  const data = await get<unknown>(`/transactions/${encodeURIComponent(id)}`);
  return transactionSchema.parse(data);
}

export interface ListTransactionsParams {
  limit?: number;
  offset?: number;
  /** Exact current-state filter (backend validates against ALL_STATES). */
  state?: string;
  /** ISO datetimes — axios percent-encodes the "+" offset correctly. */
  date_from?: string;
  date_to?: string;
}

/**
 * GET /transactions — newest first. CUSTOMER callers are always scoped to
 * their own transactions server-side (any user_id param is staff-only).
 */
export async function listTransactions(
  params: ListTransactionsParams = {}
): Promise<TransactionListResponse> {
  const data = await get<unknown>("/transactions", {
    params: Object.fromEntries(
      Object.entries(params).filter(([, value]) => value !== undefined)
    ),
  });
  return transactionListResponseSchema.parse(data);
}

/**
 * GET /transactions/summary — role-scoped aggregates: a customer's own
 * totals, or platform-wide totals for staff.
 */
export async function getTransactionsSummary(): Promise<TransactionsSummary> {
  const data = await get<unknown>("/transactions/summary");
  return transactionsSummarySchema.parse(data);
}

/** GET /transactions/{id}/timeline — events ordered ascending by the backend. */
export async function getTimeline(id: string): Promise<TimelineResponse> {
  const data = await get<unknown>(`/transactions/${encodeURIComponent(id)}/timeline`);
  return timelineResponseSchema.parse(data);
}

/** GET /transactions/{id}/reconstruction — the backend scopes it to the
 * caller's own transactions (403 otherwise), 404 for unknown transactions. */
export async function getReconstruction(id: string): Promise<ReconstructionResult> {
  const data = await get<unknown>(`/transactions/${encodeURIComponent(id)}/reconstruction`);
  return reconstructionResultSchema.parse(data);
}

/** GET /stats/summary — blocked for CUSTOMER by the backend (403). */
export async function getStats(): Promise<StatsSummary> {
  const data = await get<unknown>("/stats/summary");
  return statsSummarySchema.parse(data);
}

/**
 * GET /transactions/{id}/risk-assessment — returns null (not a thrown error)
 * when the backend reports 404 (unknown transaction, or no assessment yet),
 * mirroring the reconstruction panel's compact-empty handling.
 */
export async function getRiskAssessment(id: string): Promise<RiskAssessmentResponse | null> {
  try {
    const data = await get<unknown>(`/transactions/${encodeURIComponent(id)}/risk-assessment`);
    return riskAssessmentResponseSchema.parse(data);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return null;
    }
    throw error;
  }
}

/** POST /transactions/{id}/risk-assessment — roles SYSTEM/ADMIN. */
export async function runRiskAssessment(
  id: string,
  customerReportedFailure = false
): Promise<RiskAssessmentResponse> {
  const data = await post<unknown>(`/transactions/${encodeURIComponent(id)}/risk-assessment`, {
    customer_reported_failure: customerReportedFailure,
  });
  return riskAssessmentResponseSchema.parse(data);
}

/**
 * GET /transactions/{id}/recovery — returns null (not a thrown error) when the
 * backend reports 404 (no autonomous recovery yet), mirroring getRiskAssessment.
 * 403 (CUSTOMER role) propagates so the panel can show a muted role line.
 */
export async function getRecovery(id: string): Promise<RecoveryRecord | null> {
  try {
    const data = await get<unknown>(`/transactions/${encodeURIComponent(id)}/recovery`);
    return recoveryRecordSchema.parse(data);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return null;
    }
    throw error;
  }
}

/** POST /transactions/{id}/recovery/process — roles SYSTEM/ADMIN. */
export async function processRecovery(id: string): Promise<RecoveryOutcome> {
  const data = await post<unknown>(
    `/transactions/${encodeURIComponent(id)}/recovery/process`,
    {}
  );
  return recoveryOutcomeSchema.parse(data);
}

/** POST /transactions/{id}/recovery/evaluate — roles SYSTEM/ADMIN/SUPPORT. */
export async function evaluateRecovery(id: string): Promise<RecoveryEvaluateResult> {
  const data = await post<unknown>(
    `/transactions/${encodeURIComponent(id)}/recovery/evaluate`,
    {}
  );
  return recoveryEvaluateResultSchema.parse(data);
}
