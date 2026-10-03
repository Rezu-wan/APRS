import { useMutation, useQueryClient } from "@tanstack/react-query";
import { releaseLimit } from "../api/recovery";
import { requestExplanation } from "../api/explanations";
import { processRecovery, runRiskAssessment } from "../api/transactions";
import { fileCustomerReport, type FileCustomerReportInput } from "../api/customerReports";
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

/**
 * Run the autonomous recovery engine for a transaction (simulated sandbox —
 * no real money moves). Never optimistic — the panel reflects the result only
 * after the backend responds. Processing can transition the transaction's
 * state and produce a fresh risk assessment, so all related caches refresh.
 */
export function useProcessRecovery(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => processRecovery(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.recovery(id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.timeline(id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.transaction(id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.stats });
      void queryClient.invalidateQueries({ queryKey: queryKeys.riskAssessment(id) });
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

/**
 * Run the hybrid (rules + ML) risk assessment for a transaction.
 * Never optimistic — the panel reflects the result only after the backend
 * responds; the timeline/stats/transaction caches are refreshed because
 * running an assessment can transition the transaction's state.
 */
export function useRunAssessment(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (customerReportedFailure: boolean) =>
      runRiskAssessment(id, customerReportedFailure),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.riskAssessment(id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.timeline(id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.stats });
      void queryClient.invalidateQueries({ queryKey: queryKeys.transaction(id) });
    },
  });
}

/**
 * File (or idempotently replay) the customer's problem report. Evidence only:
 * refreshes the report + timeline caches (a twin observation is appended) but
 * never a decision cache — reports cannot change transaction state.
 */
export function useFileCustomerReport(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: Omit<FileCustomerReportInput, "transactionId">) =>
      fileCustomerReport({ transactionId: id, ...input }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.customerReport(id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.timeline(id) });
    },
  });
}
