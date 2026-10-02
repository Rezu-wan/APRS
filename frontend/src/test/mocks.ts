// vi.mock factories for the five API modules.
// IMPORTANT: import this module (or a module that imports it) as the FIRST
// import in a test file so the mocks are registered before pages load.
import { vi } from "vitest";
import { ApiError } from "../api/client";
import type { ExplanationResponse, MeResponse, RecoveryDecisionResponse, ReconstructionResult, StatsSummary, TimelineResponse, Transaction } from "../types/api";

vi.mock("../api/auth", () => ({
  getMe: vi.fn(),
  getMeWithKey: vi.fn(),
}));

vi.mock("../api/transactions", () => ({
  getTransaction: vi.fn(),
  getTimeline: vi.fn(),
  getStats: vi.fn(),
  getReconstruction: vi.fn(),
}));

vi.mock("../api/recovery", () => ({
  releaseLimit: vi.fn(),
}));

vi.mock("../api/explanations", () => ({
  requestExplanation: vi.fn(),
}));

// The vi.mock calls above are hoisted before these dynamic imports, so each
// awaited module is the mocked one — the exported handles ARE the mock fns.
const authMod = await import("../api/auth");
const transactionsMod = await import("../api/transactions");
const recoveryMod = await import("../api/recovery");
const explanationsMod = await import("../api/explanations");

export const mockGetMe = vi.mocked(authMod.getMe) as unknown as {
  (apiKey?: string): Promise<MeResponse>;
  mockResolvedValue: (v: MeResponse) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReturnValue: (v: unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};
export const mockGetMeWithKey = vi.mocked(
  authMod.getMeWithKey
) as unknown as {
  (apiKey: string): Promise<MeResponse>;
  mockResolvedValue: (v: MeResponse) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

export const mockGetTransaction = vi.mocked(transactionsMod.getTransaction) as unknown as {
  (id: string): Promise<Transaction>;
  mockResolvedValue: (v: Transaction) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockReturnValue: (v: Promise<Transaction>) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};
export const mockGetTimeline = vi.mocked(transactionsMod.getTimeline) as unknown as {
  (id: string): Promise<TimelineResponse>;
  mockResolvedValue: (v: TimelineResponse) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockReturnValue: (v: Promise<TimelineResponse>) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};
export const mockGetStats = vi.mocked(transactionsMod.getStats) as unknown as {
  (): Promise<StatsSummary>;
  mockResolvedValue: (v: StatsSummary) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockReturnValue: (v: Promise<StatsSummary>) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};
export const mockGetReconstruction = vi.mocked(
  transactionsMod.getReconstruction
) as unknown as {
  (id: string): Promise<ReconstructionResult>;
  mockResolvedValue: (v: ReconstructionResult) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockReturnValue: (v: Promise<ReconstructionResult>) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

export const mockReleaseLimit = vi.mocked(recoveryMod.releaseLimit) as unknown as {
  (id: string): Promise<RecoveryDecisionResponse>;
  mockResolvedValue: (v: RecoveryDecisionResponse) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockReturnValue: (v: Promise<RecoveryDecisionResponse>) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

export const mockRequestExplanation = vi.mocked(
  explanationsMod.requestExplanation
) as unknown as {
  (req: {
    transactionId: string;
    language: string;
    audience: string;
  }): Promise<ExplanationResponse>;
  mockResolvedValue: (v: ExplanationResponse) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockReturnValue: (v: Promise<ExplanationResponse>) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function apiError(status: number, code: string, message: string): ApiError {
  return new ApiError(status, code, message);
}

/**
 * Default behaviour for getReconstruction: the backend returns 404 when no
 * reconstruction exists, which the panel renders as a compact muted line (no
 * error alert). Existing tests that do not care about reconstruction keep
 * passing without extra mock setup.
 */
function defaultReconstruction(): Promise<ReconstructionResult> {
  return Promise.reject(new ApiError(404, "NOT_FOUND", "Transaction not found"));
}

/** A promise that never settles — used to hold requests in-flight. */
export function neverPromise<T>(): Promise<T> {
  return new Promise<T>(() => {});
}

/** Reset every mock's calls/impls between tests (call in beforeEach). */
export function resetApiMocks(): void {
  mockGetMe.mockReset();
  mockGetMeWithKey.mockReset();
  mockGetTransaction.mockReset();
  mockGetTimeline.mockReset();
  mockGetStats.mockReset();
  mockGetReconstruction.mockReset();
  mockGetReconstruction.mockImplementation(defaultReconstruction as never);
  mockReleaseLimit.mockReset();
  mockRequestExplanation.mockReset();
}
