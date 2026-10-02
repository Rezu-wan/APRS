import { useQuery } from "@tanstack/react-query";
import {
  getReconstruction,
  getRecovery,
  getRiskAssessment,
  getStats,
  getTimeline,
  getTransaction,
} from "../api/transactions";

/** Centralized query-key factories — keep invalidation keys in sync with these. */
export const queryKeys = {
  stats: ["stats"] as const,
  transaction: (id: string) => ["transaction", id] as const,
  timeline: (id: string) => ["timeline", id] as const,
  reconstruction: (id: string) => ["reconstruction", id] as const,
  riskAssessment: (id: string) => ["risk-assessment", id] as const,
  recovery: (id: string) => ["recovery", id] as const,
};

export function useStats() {
  return useQuery({
    queryKey: queryKeys.stats,
    queryFn: getStats,
  });
}

export function useTransaction(id: string) {
  return useQuery({
    queryKey: queryKeys.transaction(id),
    queryFn: () => getTransaction(id),
    enabled: id.length > 0,
  });
}

export function useTimeline(id: string) {
  return useQuery({
    queryKey: queryKeys.timeline(id),
    queryFn: () => getTimeline(id),
    enabled: id.length > 0,
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
