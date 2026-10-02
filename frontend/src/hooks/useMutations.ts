import { useMutation, useQueryClient } from "@tanstack/react-query";
import { releaseLimit } from "../api/recovery";
import { requestExplanation } from "../api/explanations";
import { queryKeys } from "./useQueries";
import type { Audience, Language } from "../types/api";

/**
 * Release-limit decision for a RECOVERY_PENDING transaction.
 * Never optimistic — the UI only reflects state after the backend responds.
 */
export function useReleaseLimit(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => releaseLimit(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.transaction(id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.timeline(id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.stats });
    },
  });
}

export interface ExplanationVariables {
  language: Language;
  audience: Audience;
}

export function useExplanation(id: string) {
  return useMutation({
    mutationFn: (variables: ExplanationVariables) =>
      requestExplanation({ transactionId: id, ...variables }),
  });
}
