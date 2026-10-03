import { ApiError, get, post } from "./client";
import {
  customerReportFileResponseSchema,
  customerReportSchema,
  type CustomerReport,
  type CustomerReportFileResponse,
  type ProblemStage,
  type ProblemType,
} from "../types/api";

export interface FileCustomerReportInput {
  transactionId: string;
  problemType: ProblemType;
  stage: ProblemStage;
  description: string;
}

/**
 * POST /transactions/{id}/customer-report — files (or idempotently replays)
 * the customer's problem report. Evidence only: never changes transaction
 * state or a recovery decision.
 */
export async function fileCustomerReport({
  transactionId,
  problemType,
  stage,
  description,
}: FileCustomerReportInput): Promise<CustomerReportFileResponse> {
  const data = await post<unknown>(
    `/transactions/${encodeURIComponent(transactionId)}/customer-report`,
    {
      problem_type: problemType,
      stage,
      description,
    }
  );
  return customerReportFileResponseSchema.parse(data);
}

/**
 * GET /transactions/{id}/customer-report — returns null (not a thrown error)
 * when the backend reports 404 (no report filed yet), mirroring the other
 * optional reads.
 */
export async function getCustomerReport(id: string): Promise<CustomerReport | null> {
  try {
    const data = await get<unknown>(
      `/transactions/${encodeURIComponent(id)}/customer-report`
    );
    return customerReportSchema.parse(data);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return null;
    }
    throw error;
  }
}
