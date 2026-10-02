import { get } from "./client";
import {
  reconstructionResultSchema,
  statsSummarySchema,
  timelineResponseSchema,
  transactionSchema,
  type ReconstructionResult,
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
