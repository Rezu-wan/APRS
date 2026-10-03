import { useQuery } from "@tanstack/react-query";
import {
  getReconstruction,
  getRecovery,
  getRiskAssessment,
  getStats,
  getTimeline,
  getTransaction,
  getTransactionsSummary,
  listTransactions,
  type ListTransactionsParams,
} from "../api/transactions";
import { getCustomerReport } from "../api/customerReports";
import { getMyProfile } from "../api/customers";

/** Centralized query-key factories — keep invalidation keys in sync with these. */
export const queryKeys = {
  stats: ["stats"] as const,
  transaction: (id: string) => ["transaction", id] as const,
  transactionList: (params: ListTransactionsParams) => ["transactions", params] as const,
  transactionsSummary: ["transactions-summary"] as const,
  customerReport: (id: string) => ["customer-report", id] as const,
  customerProfile: ["customer-profile"] as const,
  timeline: (id: string) => ["timeline", id] as const,
  reconstruction: (id: string) => ["reconstruction", id] as const,
  riskAssessment: (id: string) => ["risk-assessment", id] as const,
  recovery: (id: string) => ["recovery", id] as const,
  // Stage 10 demo control (see hooks/useDemo.ts)
  demoScenarios: ["demo", "scenarios"] as const,
  demoStatus: ["demo", "status"] as const,
  sandboxLedger: ["sandbox", "ledger"] as const,
  // Stage 11 research/ops (see hooks/useStage11.ts)
  temporalState: (id: string, ts: string) => ["temporal", id, ts] as const,
  behavioral: (id: string) => ["behavioral", id] as const,
  relationships: (id: string) => ["relationships", id] as const,
  chaosScenarios: ["chaos", "scenarios"] as const,
  // Stage 12 support workspace (see hooks/useSupport.ts) — every key starts
  // with "support" so the SSE hook can invalidate the whole workspace at once
  supportOverview: ["support", "overview"] as const,
  supportSearch: (q: string) => ["support", "search", q] as const,
  supportCustomer: (id: string) => ["support", "customer", id] as const,
  supportTransactions: (filters: Record<string, unknown>) =>
    ["support", "transactions", filters] as const,
  supportCases: (filters: Record<string, unknown>) =>
    ["support", "cases", filters] as const,
};

export function useStats() {
  return useQuery({
    queryKey: queryKeys.stats,
    queryFn: getStats,
  });
}

/** Role-scoped transaction list (CUSTOMER callers get their own data). */
export function useTransactionList(params: ListTransactionsParams) {
  return useQuery({
    queryKey: queryKeys.transactionList(params),
    queryFn: () => listTransactions(params),
  });
}

/** Role-scoped aggregate totals (customer dashboard statistics). */
export function useTransactionsSummary() {
  return useQuery({
    queryKey: queryKeys.transactionsSummary,
    queryFn: getTransactionsSummary,
  });
}

/** The customer's problem report for a transaction; null when none filed. */
export function useCustomerReport(id: string) {
  return useQuery({
    queryKey: queryKeys.customerReport(id),
    queryFn: () => getCustomerReport(id),
    enabled: id.length > 0,
  });
}

/** Profile of the signed-in customer (null when the identity has no dataset
 * profile). Only mounted for CUSTOMER callers. */
export function useCustomerProfile() {
  return useQuery({
    queryKey: queryKeys.customerProfile,
    queryFn: () => getMyProfile(),
  });
}

export function useTransaction(id: string) {
  return useQuery({
    queryKey: queryKeys.transaction(id),
    queryFn: () => getTransaction(id),
    enabled: id.length > 0,
  });
}

export function useTimeline(id: string, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.timeline(id),
    queryFn: () => getTimeline(id),
    enabled: id.length > 0 && (options.enabled ?? true),
  });
}

export function useReconstruction(id: string) {
  return useQuery({
    queryKey: queryKeys.reconstruction(id),
    queryFn: () => getReconstruction(id),
    enabled: id.length > 0,
  });
}

export function useRiskAssessment(id: string) {
  return useQuery({
    queryKey: queryKeys.riskAssessment(id),
    queryFn: () => getRiskAssessment(id),
    enabled: id.length > 0,
  });
}

/** GET …/recovery — 404 already mapped to null by the API layer. */
export function useRecovery(id: string) {
  return useQuery({
    queryKey: queryKeys.recovery(id),
    queryFn: () => getRecovery(id),
    enabled: id.length > 0,
  });
}
