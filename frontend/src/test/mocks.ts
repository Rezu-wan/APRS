// vi.mock factories for the five API modules.
// IMPORTANT: import this module (or a module that imports it) as the FIRST
// import in a test file so the mocks are registered before pages load.
import { vi } from "vitest";
import { ApiError } from "../api/client";
import type { ExplanationResponse, MeResponse, RecoveryDecisionResponse, RecoveryOutcome, RecoveryRecord, ReconstructionResult, RiskAssessmentResponse, StatsSummary, TimelineResponse, Transaction } from "../types/api";

vi.mock("../api/auth", () => ({
  getMe: vi.fn(),
  getMeWithKey: vi.fn(),
}));

vi.mock("../api/transactions", () => ({
  getTransaction: vi.fn(),
  getTimeline: vi.fn(),
  getStats: vi.fn(),
  listTransactions: vi.fn(),
  getTransactionsSummary: vi.fn(),
  getReconstruction: vi.fn(),
  getRiskAssessment: vi.fn(),
  runRiskAssessment: vi.fn(),
  getRecovery: vi.fn(),
  processRecovery: vi.fn(),
}));

vi.mock("../api/customerReports", () => ({
  fileCustomerReport: vi.fn(),
  getCustomerReport: vi.fn(),
}));

vi.mock("../api/customers", () => ({
  getMyProfile: vi.fn(),
}));

vi.mock("../api/recovery", () => ({
  releaseLimit: vi.fn(),
}));

vi.mock("../api/explanations", () => ({
  requestExplanation: vi.fn(),
}));

vi.mock("../api/demo", async (importOriginal) => {
  // Keep the display-only helpers (humanizeBlockedReason) real — only the
  // network functions are mocked.
  const actual = await importOriginal<typeof import("../api/demo")>();
  return {
    ...actual,
    getDemoScenarios: vi.fn(),
    getDemoStatus: vi.fn(),
    getSandboxLedger: vi.fn(),
    prepareScenario: vi.fn(),
    injectLateSettlement: vi.fn(),
    resetDemo: vi.fn(),
  };
});

vi.mock("../api/stage11", async (importOriginal) => {
  // Keep humanizeVerdict real; mock the network functions.
  const actual = await importOriginal<typeof import("../api/stage11")>();
  return {
    ...actual,
    getStateAt: vi.fn(),
    getBehavioralSignals: vi.fn(),
    getRelationships: vi.fn(),
    runPolicySimulation: vi.fn(),
    getChaosScenarios: vi.fn(),
    runChaosScenario: vi.fn(),
  };
});

// The vi.mock calls above are hoisted before these dynamic imports, so each
// awaited module is the mocked one — the exported handles ARE the mock fns.
const authMod = await import("../api/auth");
const transactionsMod = await import("../api/transactions");
const customerReportsMod = await import("../api/customerReports");
const customersMod = await import("../api/customers");
const recoveryMod = await import("../api/recovery");
const explanationsMod = await import("../api/explanations");
const demoMod = await import("../api/demo");
const stage11Mod = await import("../api/stage11");

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

export const mockListTransactions = vi.mocked(
  transactionsMod.listTransactions
) as unknown as {
  (params?: import("../api/transactions").ListTransactionsParams): Promise<import("../types/api").TransactionListResponse>;
  mockResolvedValue: (v: import("../types/api").TransactionListResponse) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

export const mockGetTransactionsSummary = vi.mocked(
  transactionsMod.getTransactionsSummary
) as unknown as {
  (): Promise<import("../types/api").TransactionsSummary>;
  mockResolvedValue: (v: import("../types/api").TransactionsSummary) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

export const mockGetCustomerReport = vi.mocked(
  customerReportsMod.getCustomerReport
) as unknown as {
  (id: string): Promise<import("../types/api").CustomerReport | null>;
  mockResolvedValue: (v: import("../types/api").CustomerReport | null) => unknown;
  mockResolvedValueOnce: (v: import("../types/api").CustomerReport | null) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

export const mockGetMyProfile = vi.mocked(customersMod.getMyProfile) as unknown as {
  (): Promise<import("../types/api").CustomerProfile | null>;
  mockResolvedValue: (v: import("../types/api").CustomerProfile | null) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

export const mockFileCustomerReport = vi.mocked(
  customerReportsMod.fileCustomerReport
) as unknown as {
  (input: import("../api/customerReports").FileCustomerReportInput): Promise<import("../types/api").CustomerReportFileResponse>;
  mockResolvedValue: (v: import("../types/api").CustomerReportFileResponse) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
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
export const mockGetRiskAssessment = vi.mocked(
  transactionsMod.getRiskAssessment
) as unknown as {
  (id: string): Promise<RiskAssessmentResponse | null>;
  mockResolvedValue: (v: RiskAssessmentResponse | null) => unknown;
  mockResolvedValueOnce: (v: RiskAssessmentResponse | null) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockReturnValue: (v: Promise<RiskAssessmentResponse | null>) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};
export const mockRunRiskAssessment = vi.mocked(
  transactionsMod.runRiskAssessment
) as unknown as {
  (id: string, customerReportedFailure?: boolean): Promise<RiskAssessmentResponse>;
  mockResolvedValue: (v: RiskAssessmentResponse) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockReturnValue: (v: Promise<RiskAssessmentResponse>) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

export const mockGetRecovery = vi.mocked(
  transactionsMod.getRecovery
) as unknown as {
  (id: string): Promise<RecoveryRecord | null>;
  mockResolvedValue: (v: RecoveryRecord | null) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockReturnValue: (v: Promise<RecoveryRecord | null>) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

export const mockProcessRecovery = vi.mocked(
  transactionsMod.processRecovery
) as unknown as {
  (id: string): Promise<RecoveryOutcome>;
  mockResolvedValue: (v: RecoveryOutcome) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockReturnValue: (v: Promise<RecoveryOutcome>) => unknown;
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
// Stage 10 demo-control mocks (src/api/demo.ts)
// ---------------------------------------------------------------------------

export const mockGetDemoScenarios = vi.mocked(demoMod.getDemoScenarios) as unknown as {
  (): Promise<import("../api/demo").DemoScenariosResponse>;
  mockResolvedValue: (v: import("../api/demo").DemoScenariosResponse) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockReturnValue: (v: Promise<import("../api/demo").DemoScenariosResponse>) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

export const mockGetDemoStatus = vi.mocked(demoMod.getDemoStatus) as unknown as {
  (): Promise<import("../api/demo").DemoStatusResponse>;
  mockResolvedValue: (v: import("../api/demo").DemoStatusResponse) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockReturnValue: (v: Promise<import("../api/demo").DemoStatusResponse>) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

export const mockGetSandboxLedger = vi.mocked(demoMod.getSandboxLedger) as unknown as {
  (): Promise<import("../api/demo").SandboxLedgerResponse>;
  mockResolvedValue: (v: import("../api/demo").SandboxLedgerResponse) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockReturnValue: (v: Promise<import("../api/demo").SandboxLedgerResponse>) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

export const mockPrepareScenario = vi.mocked(demoMod.prepareScenario) as unknown as {
  (key: string): Promise<import("../api/demo").DemoPrepareResponse>;
  mockResolvedValue: (v: import("../api/demo").DemoPrepareResponse) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

export const mockInjectLateSettlement = vi.mocked(
  demoMod.injectLateSettlement
) as unknown as {
  (key: string): Promise<import("../api/demo").DemoPrepareResponse>;
  mockResolvedValue: (v: import("../api/demo").DemoPrepareResponse) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

export const mockResetDemo = vi.mocked(demoMod.resetDemo) as unknown as {
  (): Promise<import("../api/demo").DemoResetResponse>;
  mockResolvedValue: (v: import("../api/demo").DemoResetResponse) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

// ---------------------------------------------------------------------------
// Stage 11 mocks (src/api/stage11.ts)
// ---------------------------------------------------------------------------

export const mockGetStateAt = vi.mocked(stage11Mod.getStateAt) as unknown as {
  (id: string, ts: string): Promise<import("../api/stage11").TemporalStateReport>;
  mockResolvedValue: (v: import("../api/stage11").TemporalStateReport) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

export const mockGetBehavioralSignals = vi.mocked(
  stage11Mod.getBehavioralSignals
) as unknown as {
  (id: string): Promise<import("../api/stage11").BehavioralReport>;
  mockResolvedValue: (v: import("../api/stage11").BehavioralReport) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockReturnValue: (v: Promise<import("../api/stage11").BehavioralReport>) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

export const mockGetRelationships = vi.mocked(
  stage11Mod.getRelationships
) as unknown as {
  (id: string): Promise<import("../api/stage11").RelationshipResponse>;
  mockResolvedValue: (v: import("../api/stage11").RelationshipResponse) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockReturnValue: (v: Promise<import("../api/stage11").RelationshipResponse>) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

export const mockRunPolicySimulation = vi.mocked(
  stage11Mod.runPolicySimulation
) as unknown as {
  (req: import("../api/stage11").SimulatorRunRequest): Promise<import("../api/stage11").PolicySimulationRun>;
  mockResolvedValue: (v: import("../api/stage11").PolicySimulationRun) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

export const mockGetChaosScenarios = vi.mocked(
  stage11Mod.getChaosScenarios
) as unknown as {
  (): Promise<import("../api/stage11").ChaosScenarioListResponse>;
  mockResolvedValue: (v: import("../api/stage11").ChaosScenarioListResponse) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
  mockImplementation: (fn: (...args: unknown[]) => unknown) => unknown;
  mockReset: () => unknown;
  mock: { calls: unknown[][] };
};

export const mockRunChaosScenario = vi.mocked(
  stage11Mod.runChaosScenario
) as unknown as {
  (scenario: string): Promise<import("../api/stage11").ChaosRunResult>;
  mockResolvedValue: (v: import("../api/stage11").ChaosRunResult) => unknown;
  mockRejectedValue: (v: unknown) => unknown;
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

/**
 * Default behaviour for getRiskAssessment: the backend reports 404 when no
 * assessment exists; the API layer turns that into `null`, which the panel
 * renders as a compact muted line (plus a Run-assessment button for staff).
 */
function defaultRiskAssessment(): Promise<RiskAssessmentResponse | null> {
  return Promise.resolve(null);
}

function defaultRunRiskAssessment(): Promise<RiskAssessmentResponse> {
  return Promise.reject(new ApiError(500, "UNKNOWN", "runRiskAssessment not stubbed"));
}

/** Default for getRecovery: 404 -> null (no recovery yet) — mirrors the API layer. */
function defaultGetRecovery(): Promise<import("../types/api").RecoveryRecord | null> {
  return Promise.resolve(null);
}

function defaultProcessRecovery(): Promise<import("../types/api").RecoveryOutcome> {
  return Promise.reject(new ApiError(500, "UNKNOWN", "processRecovery not stubbed"));
}

// Customer-dashboard defaults — an empty scoped list/summary and no report,
// so tests that don't care about them stay quiet.
function defaultListTransactions(): Promise<import("../types/api").TransactionListResponse> {
  return Promise.resolve({ items: [], total: 0, limit: 50, offset: 0 });
}

function defaultTransactionsSummary(): Promise<import("../types/api").TransactionsSummary> {
  return Promise.resolve({ total: 0, by_state: {}, amounts_by_currency: {} });
}

function defaultGetCustomerReport(): Promise<import("../types/api").CustomerReport | null> {
  return Promise.resolve(null);
}

function defaultFileCustomerReport(): Promise<import("../types/api").CustomerReportFileResponse> {
  return Promise.reject(new ApiError(500, "UNKNOWN", "fileCustomerReport not stubbed"));
}

function defaultGetMyProfile(): Promise<import("../types/api").CustomerProfile | null> {
  return Promise.resolve(null);
}

// Stage 10 demo defaults — pages must handle an empty scenario list, and the
// console renders the shape below when no test overrides it.
function defaultDemoScenarios(): Promise<import("../api/demo").DemoScenariosResponse> {
  return Promise.resolve({ simulated: true, scenarios: [] });
}

function defaultDemoStatus(): Promise<import("../api/demo").DemoStatusResponse> {
  return Promise.resolve({
    database: "connected",
    ml_models: "loaded",
    genai: { provider: "mock", status: "available", prompt_version: "v4" },
    sandbox_provider: {
      provider: "mock",
      initial_limit: 10000,
      available_limit: 10000,
      currency: "BDT",
      held_entries: 0,
      released_entries: 0,
    },
  });
}

function defaultSandboxLedger(): Promise<import("../api/demo").SandboxLedgerResponse> {
  return Promise.resolve({
    simulated: true,
    initial_limit: 10000,
    available_limit: 10000,
    currency: "BDT",
    entries: [],
  });
}

function defaultPrepareScenario(): Promise<import("../api/demo").DemoPrepareResponse> {
  return Promise.reject(new ApiError(500, "UNKNOWN", "prepareScenario not stubbed"));
}

function defaultInjectLateSettlement(): Promise<import("../api/demo").DemoPrepareResponse> {
  return Promise.reject(new ApiError(500, "UNKNOWN", "injectLateSettlement not stubbed"));
}

function defaultResetDemo(): Promise<import("../api/demo").DemoResetResponse> {
  return Promise.reject(new ApiError(500, "UNKNOWN", "resetDemo not stubbed"));
}

// Stage 11 defaults — staff-only reads; the panels render 403 as a muted
// role line, so tests that don't care about them stay quiet.
function defaultStaffForbidden(): Promise<never> {
  return Promise.reject(new ApiError(403, "INSUFFICIENT_PERMISSIONS", "forbidden"));
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
  mockGetRiskAssessment.mockReset();
  mockGetRiskAssessment.mockImplementation(defaultRiskAssessment as never);
  mockRunRiskAssessment.mockReset();
  mockRunRiskAssessment.mockImplementation(defaultRunRiskAssessment as never);
  mockGetRecovery.mockReset();
  mockGetRecovery.mockImplementation(defaultGetRecovery as never);
  mockProcessRecovery.mockReset();
  mockProcessRecovery.mockImplementation(defaultProcessRecovery as never);
  mockListTransactions.mockReset();
  mockListTransactions.mockImplementation(defaultListTransactions as never);
  mockGetTransactionsSummary.mockReset();
  mockGetTransactionsSummary.mockImplementation(defaultTransactionsSummary as never);
  mockGetCustomerReport.mockReset();
  mockGetCustomerReport.mockImplementation(defaultGetCustomerReport as never);
  mockFileCustomerReport.mockReset();
  mockFileCustomerReport.mockImplementation(defaultFileCustomerReport as never);
  mockGetMyProfile.mockReset();
  mockGetMyProfile.mockImplementation(defaultGetMyProfile as never);
  mockReleaseLimit.mockReset();
  mockRequestExplanation.mockReset();
  mockGetDemoScenarios.mockReset();
  mockGetDemoScenarios.mockImplementation(defaultDemoScenarios as never);
  mockGetDemoStatus.mockReset();
  mockGetDemoStatus.mockImplementation(defaultDemoStatus as never);
  mockGetSandboxLedger.mockReset();
  mockGetSandboxLedger.mockImplementation(defaultSandboxLedger as never);
  mockPrepareScenario.mockReset();
  mockPrepareScenario.mockImplementation(defaultPrepareScenario as never);
  mockInjectLateSettlement.mockReset();
  mockInjectLateSettlement.mockImplementation(defaultInjectLateSettlement as never);
  mockResetDemo.mockReset();
  mockResetDemo.mockImplementation(defaultResetDemo as never);
  mockGetStateAt.mockReset();
  mockGetStateAt.mockImplementation((() => defaultStaffForbidden()) as never);
  mockGetBehavioralSignals.mockReset();
  mockGetBehavioralSignals.mockImplementation((() => defaultStaffForbidden()) as never);
  mockGetRelationships.mockReset();
  mockGetRelationships.mockImplementation((() => defaultStaffForbidden()) as never);
  mockRunPolicySimulation.mockReset();
  mockRunPolicySimulation.mockImplementation(
    () => Promise.reject(new ApiError(500, "UNKNOWN", "runPolicySimulation not stubbed")) as never
  );
  mockGetChaosScenarios.mockReset();
  mockGetChaosScenarios.mockImplementation(
    () => Promise.resolve({ scenarios: [] }) as never
  );
  mockRunChaosScenario.mockReset();
  mockRunChaosScenario.mockImplementation(
    () => Promise.reject(new ApiError(500, "UNKNOWN", "runChaosScenario not stubbed")) as never
  );
}
