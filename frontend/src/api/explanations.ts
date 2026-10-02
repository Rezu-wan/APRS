import { post } from "./client";
import {
  explanationResponseSchema,
  type Audience,
  type ExplanationResponse,
  type Language,
} from "../types/api";

export interface ExplanationRequest {
  transactionId: string;
  language: Language;
  audience: Audience;
}

/** POST /explanations/transaction — generate an explanation for a transaction. */
export async function requestExplanation({
  transactionId,
  language,
  audience,
}: ExplanationRequest): Promise<ExplanationResponse> {
  const data = await post<unknown>("/explanations/transaction", {
    transaction_id: transactionId,
    language,
    audience,
  });
  return explanationResponseSchema.parse(data);
}
