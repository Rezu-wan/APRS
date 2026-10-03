import "../test/mocks";

import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PolicySimulator from "../pages/PolicySimulator";
import type { PolicySimulationRun } from "../api/stage11";
import { mockRunPolicySimulation, resetApiMocks } from "../test/mocks";
import { renderWithProviders } from "../test/utils";

afterEach(() => cleanup());
beforeEach(() => {
  resetApiMocks();
  sessionStorage.clear();
});

function makeDecision(transactionId: string, overrides: Record<string, unknown> = {}) {
  return {
    transaction_id: transactionId,
    action: "RELEASE_LIMIT",
    blocked_reason: null,
    eligible: true,
    gate_allowed: true,
    gate_blocked_reason: null,
    gate_vetoed: false,
    ...overrides,
  };
}

function makePolicyResult(version: string, overrides: Record<string, unknown> = {}) {
  return {
    policy_version: version,
    description: `Description for ${version}`,
    experimental: version === "autonomous-v2-experimental",
    transactions_evaluated: 6,
    would_release: 3,
    would_block: 1,
    would_manual_review: 1,
    would_no_action: 1,
    gate_vetoes: 1,
    false_recovery: null,
    missed_recovery: null,
    provider_calls_avoided: 2,
    decision_latency_ms_avg: 12.5,
    decisions: [
      makeDecision("DEMO-S1"),
      makeDecision("DEMO-S2", {
        action: "BLOCKED",
        blocked_reason: "MULTIPLE_DEBITS",
        eligible: false,
        gate_allowed: false,
        gate_blocked_reason: "MULTIPLE_DEBITS",
        gate_vetoed: true,
      }),
    ],
    ...overrides,
  };
}

function makeRun(overrides: Partial<PolicySimulationRun> = {}): PolicySimulationRun {
  return {
    run_id: "run-1234567890abcdef",
    simulated: true,
    affects_live_policy: false,
    ground_truth_available: true,
    dataset: {
      source: "demo",
      transaction_ids: ["DEMO-S1", "DEMO-S2", "DEMO-S3", "DEMO-S4", "DEMO-S5", "DEMO-S6"],
      dataset_fingerprint: "fp-abcdef1234567890",
    },
    policies: [
      makePolicyResult("autonomous-v1"),
      makePolicyResult("manual-only-baseline", {
        would_release: 0,
        would_block: 0,
        would_manual_review: 6,
        would_no_action: 0,
        gate_vetoes: 0,
        provider_calls_avoided: 6,
        decisions: [],
      }),
      makePolicyResult("autonomous-v2-experimental"),
    ],
    comparison: [
      {
        metric: "would_release",
        per_policy: { "autonomous-v1": 3, "manual-only-baseline": 0 },
      },
      {
        metric: "gate_vetoes",
        per_policy: { "autonomous-v1": 1, "manual-only-baseline": 0 },
      },
    ],
    generated_at: "2026-10-03T10:00:00+00:00",
    code_versions: { policy: "v1" },
    ...overrides,
  };
}

function renderSimulator(authUser: {
  role: "SYSTEM" | "ADMIN" | "SUPPORT" | "CUSTOMER";
  keyName: string;
}) {
  return renderWithProviders(
    <Routes>
      <Route path="/simulator" element={<PolicySimulator />} />
    </Routes>,
    { route: "/simulator", authUser }
  );
}

describe("PolicySimulator", () => {
  it("lets SUPPORT view the page but not run simulations", async () => {
    renderSimulator({ role: "SUPPORT", keyName: "support-key" });

    expect(await screen.findByText("Policy simulator")).toBeInTheDocument();
    expect(
      screen.getByText("No simulation can affect the live policy or the sandbox ledger")
    ).toBeInTheDocument();
    expect(screen.queryByTestId("run-simulation")).not.toBeInTheDocument();
    expect(screen.getByText(/requires the SYSTEM or ADMIN role/)).toBeInTheDocument();
  });

  it("blocks CUSTOMER at the RoleGate", async () => {
    renderSimulator({ role: "CUSTOMER", keyName: "cust-key" });

    expect(await screen.findByText("Access denied")).toBeInTheDocument();
    expect(screen.queryByText("Policy simulator")).not.toBeInTheDocument();
  });

  it("SYSTEM runs with the demo dataset and sees cards, comparison, and the no-ranking footer", async () => {
    const run = makeRun();
    mockRunPolicySimulation.mockResolvedValue(run);
    renderSimulator({ role: "SYSTEM", keyName: "system-key" });

    await userEvent.click(await screen.findByTestId("run-simulation"));

    await waitFor(() => {
      expect(mockRunPolicySimulation).toHaveBeenCalledTimes(1);
    });
    expect(mockRunPolicySimulation.mock.calls[0]?.[0]).toEqual({
      policies: ["autonomous-v1"],
      source: { demo: true },
    });

    // Per-policy cards (version appears in both the card heading and the
    // comparison-table column header).
    expect((await screen.findAllByText("autonomous-v1")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("manual-only-baseline").length).toBeGreaterThan(0);
    expect(screen.getByText("experimental")).toBeInTheDocument();
    expect(screen.getAllByText("Provider calls avoided")).toHaveLength(3);

    // Comparison table + honest footer.
    expect(screen.getByText("would_release")).toBeInTheDocument();
    expect(
      screen.getByText(
        "The simulator reports measurable differences only; it does not rank policies."
      )
    ).toBeInTheDocument();

    // Run meta honesty.
    expect(screen.getByTestId("run-meta")).toHaveTextContent("affects live policy: no");

    // Export button appears after a run.
    expect(screen.getByTestId("export-results")).toBeInTheDocument();
  });

  it("renders the ground-truth caveat for an arbitrary corpus", async () => {
    mockRunPolicySimulation.mockResolvedValue(makeRun({ ground_truth_available: false }));
    renderSimulator({ role: "ADMIN", keyName: "admin-key" });

    await userEvent.click(await screen.findByTestId("run-simulation"));

    expect(await screen.findByText(/arbitrary corpus/)).toBeInTheDocument();
  });

  it("sends custom transaction ids when the custom dataset is selected", async () => {
    mockRunPolicySimulation.mockResolvedValue(makeRun());
    renderSimulator({ role: "SYSTEM", keyName: "system-key" });

    await userEvent.click(await screen.findByTestId("run-simulation"));
    expect(await screen.findByTestId("export-results")).toBeInTheDocument();
    const firstRequest = mockRunPolicySimulation.mock.calls[0]?.[0] as import("../api/stage11").SimulatorRunRequest;
    expect(firstRequest.source).toEqual({ demo: true });

    await userEvent.click(screen.getByLabelText("Custom transaction ids"));
    await userEvent.type(screen.getByTestId("custom-ids"), "TXN-A, TXN-B");
    await userEvent.click(screen.getByTestId("run-simulation"));

    await waitFor(() => {
      expect(mockRunPolicySimulation).toHaveBeenCalledTimes(2);
    });
    expect(mockRunPolicySimulation.mock.calls[1]?.[0]).toEqual({
      policies: ["autonomous-v1"],
      source: { demo: false, transaction_ids: ["TXN-A", "TXN-B"] },
    });
  });

  it("expands per-transaction decisions with action chip and gate veto", async () => {
    mockRunPolicySimulation.mockResolvedValue(makeRun());
    renderSimulator({ role: "SYSTEM", keyName: "system-key" });

    await userEvent.click(await screen.findByTestId("run-simulation"));
    await screen.findAllByText("autonomous-v1");

    const details = screen.getAllByText(/Per-transaction decisions \(2\)/);
    await userEvent.click(details[0]);

    expect(screen.getAllByText("DEMO-S1").length).toBeGreaterThan(0);
    expect(screen.getAllByText("gate veto")).toHaveLength(2); // one per policy card
    expect(screen.getAllByText(/blocked: MULTIPLE_DEBITS/)).toHaveLength(2);
  });

  it("exports the run JSON via a client-side blob download", async () => {
    const run = makeRun();
    mockRunPolicySimulation.mockResolvedValue(run);
    const createObjectURL = vi.fn(() => "blob:fake");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      value: createObjectURL,
      configurable: true,
      writable: true,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      value: revokeObjectURL,
      configurable: true,
      writable: true,
    });

    renderSimulator({ role: "SYSTEM", keyName: "system-key" });
    await userEvent.click(await screen.findByTestId("run-simulation"));
    await userEvent.click(await screen.findByTestId("export-results"));

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:fake");
    expect(await screen.findByTestId("export-done")).toBeInTheDocument();

    const blobArg = (createObjectURL.mock.calls as unknown[][])[0]?.[0] as Blob;
    expect(blobArg).toBeInstanceOf(Blob);
  });

  it("renders an honest ErrorState when the run fails", async () => {
    mockRunPolicySimulation.mockRejectedValue(new Error("boom"));
    renderSimulator({ role: "SYSTEM", keyName: "system-key" });

    await userEvent.click(await screen.findByTestId("run-simulation"));

    expect(await screen.findByText("Simulation failed")).toBeInTheDocument();
    expect(screen.getByText("boom")).toBeInTheDocument();
    expect(screen.queryByTestId("export-results")).not.toBeInTheDocument();
  });
});
