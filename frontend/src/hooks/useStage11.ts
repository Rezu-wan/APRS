import { useQuery } from "@tanstack/react-query";
import {
  getBehavioralSignals,
  getChaosScenarios,
  getRelationships,
  getStateAt,
  toStateAtParam,
} from "../api/stage11";
import { queryKeys } from "./useQueries";

/** Temporal twin — the transaction's state as of a past timestamp. The
 * caller owns picking `at`; results are exact per timestamp (query key
 * includes it), never refetched silently for a different instant. */
export function useTemporalState(id: string, at: Date | null) {
  return useQuery({
    queryKey: queryKeys.temporalState(id, at ? at.toISOString() : "none"),
    queryFn: () => getStateAt(id, toStateAtParam(at as Date)),
    enabled: id.length > 0 && at !== null,
    // Historical answers are immutable — cache for the session.
    staleTime: Number.POSITIVE_INFINITY,
  });
}

/** Explainable behavioral signals for a transaction (staff-only endpoint;
 * the panel renders the 403 as a muted role line). */
export function useBehavioralSignals(id: string) {
  return useQuery({
    queryKey: queryKeys.behavioral(id),
    queryFn: () => getBehavioralSignals(id),
    enabled: id.length > 0,
  });
}

/** Relationship graph signals (staff-only endpoint). */
export function useRelationships(id: string) {
  return useQuery({
    queryKey: queryKeys.relationships(id),
    queryFn: () => getRelationships(id),
    enabled: id.length > 0,
  });
}

/** Chaos scenario catalog (staff-only). */
export function useChaosScenarios() {
  return useQuery({
    queryKey: queryKeys.chaosScenarios,
    queryFn: getChaosScenarios,
  });
}
