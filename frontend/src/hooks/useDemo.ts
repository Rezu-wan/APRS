import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getDemoScenarios,
  getDemoStatus,
  getSandboxLedger,
  injectLateSettlement,
  prepareScenario,
  resetDemo,
  type DemoScenarioKey,
} from "../api/demo";
import { queryKeys } from "./useQueries";

/** Scenario list — kept fresh so the panel reflects live DB state. */
export function useDemoScenarios() {
  return useQuery({
    queryKey: queryKeys.demoScenarios,
    queryFn: getDemoScenarios,
  });
}

/** Console health block (API implicit, DB, ML, GenAI, sandbox provider). */
export function useDemoStatus() {
  return useQuery({
    queryKey: queryKeys.demoStatus,
    queryFn: getDemoStatus,
  });
}

/** Simulated sandbox ledger — available/held funds and per-transaction entries. */
export function useSandboxLedger() {
  return useQuery({
    queryKey: queryKeys.sandboxLedger,
    queryFn: getSandboxLedger,
  });
}

function useInvalidateDemo() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.demoScenarios });
    void queryClient.invalidateQueries({ queryKey: queryKeys.demoStatus });
    void queryClient.invalidateQueries({ queryKey: queryKeys.sandboxLedger });
    void queryClient.invalidateQueries({ queryKey: queryKeys.stats });
  };
}

/**
 * Prepare one demo scenario (create transaction + evidence + risk assessment
 * through the REAL services). Never optimistic; invalidates everything the
 * action can touch. Preparation never runs recovery — the panel drives the
 * actual /recovery/process endpoint for that.
 */
export function usePrepareScenario() {
  const invalidate = useInvalidateDemo();
  return useMutation({
    mutationFn: (key: DemoScenarioKey) => prepareScenario(key),
    onSuccess: invalidate,
  });
}

/** Inject the late SETTLEMENT_CONFIRMED event (S5 race scenario, S5 only). */
export function useInjectLateSettlement() {
  const invalidate = useInvalidateDemo();
  return useMutation({
    mutationFn: (key: DemoScenarioKey) => injectLateSettlement(key),
    onSuccess: invalidate,
  });
}

/**
 * Full demo reset — purges DEMO-S* data, resets the sandbox ledger, and
 * re-prepares all six scenarios. Requires confirmation in the UI; the
 * backend audits the action and it affects simulated data only.
 */
export function useResetDemo() {
  const invalidate = useInvalidateDemo();
  return useMutation({
    mutationFn: () => resetDemo(),
    onSuccess: invalidate,
  });
}
