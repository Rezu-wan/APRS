import { post } from "./client";
import { recoveryDecisionResponseSchema, type RecoveryDecisionResponse } from "../types/api";

/**
 * POST /recovery/release-limit — ask the backend to decide on a
 * RECOVERY_PENDING transaction. 409 (not recoverable) propagates as ApiError.
 */
export async function releaseLimit(transactionId: string): Promise<RecoveryDecisionResponse> {
  const data = await post<unknown>("/recovery/release-limit", {
    transaction_id: transactionId,
  });
  return recoveryDecisionResponseSchema.parse(data);
}
