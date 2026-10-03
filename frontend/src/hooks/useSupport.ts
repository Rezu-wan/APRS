import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createSupportCase,
  getSupportCases,
  getSupportCustomerProfile,
  getSupportOverview,
  getSupportTransactions,
  searchSupportCustomers,
  updateSupportCase,
  type SupportCaseCreateInput,
  type SupportCaseQuery,
  type SupportCaseUpdateInput,
  type SupportTransactionQuery,
} from "../api/support";
import { queryKeys } from "./useQueries";

/** Queue overview — counts, needs-attention, recent activity. */
export function useSupportOverview() {
  return useQuery({
    queryKey: queryKeys.supportOverview,
    queryFn: getSupportOverview,
  });
}

/** Customer search — empty query stays disabled (no wasted requests). */
export function useSupportCustomerSearch(query: string) {
  return useQuery({
    queryKey: queryKeys.supportSearch(query),
    queryFn: () => searchSupportCustomers(query),
    enabled: query.trim().length > 0,
  });
}

/** Full customer-care profile (404 surfaces as an error the page renders). */
export function useSupportCustomerProfile(customerId: string) {
  return useQuery({
    queryKey: queryKeys.supportCustomer(customerId),
    queryFn: () => getSupportCustomerProfile(customerId),
    enabled: customerId.length > 0,
  });
}

/** Support-facing transaction list (searchable/filterable). */
export function useSupportTransactions(filters: SupportTransactionQuery) {
  return useQuery({
    queryKey: queryKeys.supportTransactions(filters as Record<string, unknown>),
    queryFn: () => getSupportTransactions(filters),
  });
}

/** Case queue with filters. */
export function useSupportCases(filters: SupportCaseQuery) {
  return useQuery({
    queryKey: queryKeys.supportCases(filters as Record<string, unknown>),
    queryFn: () => getSupportCases(filters),
  });
}

function useInvalidateSupport() {
  const queryClient = useQueryClient();
  // ["support"] is a prefix of every support key (see queryKeys) — one
  // invalidation refreshes the whole workspace. Never optimistic: the case
  // state machine lives on the backend.
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["support"] });
  };
}

/** Open a new support case (customer is derived from the transaction server-side). */
export function useCreateSupportCase() {
  const invalidate = useInvalidateSupport();
  return useMutation({
    mutationFn: (input: SupportCaseCreateInput) => createSupportCase(input),
    onSuccess: invalidate,
  });
}

/** Transition/re-note a case; illegal transitions come back as clean 400s. */
export function useUpdateSupportCase() {
  const invalidate = useInvalidateSupport();
  return useMutation({
    mutationFn: ({
      caseId,
      input,
    }: {
      caseId: string;
      input: SupportCaseUpdateInput;
    }) => updateSupportCase(caseId, input),
    onSuccess: invalidate,
  });
}
