import { ApiError, get, post } from "./client";
import {
  reconstructionResultSchema,
  riskAssessmentResponseSchema,
  statsSummarySchema,
  timelineResponseSchema,
  transactionSchema,
  type ReconstructionResult,
  type RiskAssessmentResponse,
  type StatsSummary,
  type TimelineResponse,
  type Transaction,
} from "../types/api";

/** GET /transactions/{id} — 404 propagates as ApiError. */
export async function getTransaction(id: string): Promise<Transaction> {
  const data = await get<unknown>(`/transactions/${encodeURIComponent(id)}`);
  return transactionSchema.parse(data);
}

/** GET /transactions/{id}/timeline — events ordered ascending by the backend. */
export async function getTimeline(id: string): Promise<TimelineResponse> {
  const data = await get<unknown>(`/transactions/${encodeURIComponent(id)}/timeline`);
  return timelineResponseSchema.parse(data);
}

/** GET /transactions/{id}/reconstruction — blocked for CUSTOMER (403), 404 for unknown transactions. */
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
