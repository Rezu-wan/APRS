import "../test/mocks";

import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import ChaosLab from "../pages/ChaosLab";
import type { ChaosRunResult, ChaosScenario } from "../api/stage11";
import {
  mockGetChaosScenarios,
  mockRunChaosScenario,
  resetApiMocks,
} from "../test/mocks";
import { renderWithProviders } from "../test/utils";

afterEach(() => cleanup());
beforeEach(() => {
  resetApiMocks();
  sessionStorage.clear();
});

const SCENARIOS: ChaosScenario[] = [
  {
    scenario: "LATE_SETTLEMENT",
    description: "Settlement confirmation arrives after the recovery decision.",
    expected: { decision: "AUTO_RECOVERED", invariants: ">= 1" },
  },
  {
    scenario: "DB_FAILURE_SIMULATION",
    description: "Database outage during the recovery pipeline.",
    expected: { verdict: "SKIP", note: "DB_FAILURE_SIMULATION" },
  },
];

function mockCatalog() {
  mockGetChaosScenarios.mockResolvedValue({ scenarios: SCENARIOS });
}

function renderChaos(authUser: {
  role: "SYSTEM" | "ADMIN" | "SUPPORT" | "CUSTOMER";
  keyName: string;
}) {
  return renderWithProviders(
    <Routes>
      <Route path="/chaos" element={<ChaosLab />} />
    </Routes>,
    { route: "/chaos", authUser }
  );
}

describe("ChaosLab", () => {
  it("renders the scenario catalog with expected rows for staff", async () => {
    mockCatalog();
    renderChaos({ role: "SYSTEM", keyName: "system-key" });

    expect(await screen.findByText("Chaos lab")).toBeInTheDocument();
    const lateCard = await screen.findByTestId("chaos-card-LATE_SETTLEMENT");
    expect(lateCard).toBeInTheDocument();
    expect(lateCard.textContent).toContain("LATE_SETTLEMENT");
    expect(screen.getByTestId("chaos-card-DB_FAILURE_SIMULATION")).toBeInTheDocument();
    // Expected object rendered as key: value mono rows.
    expect(screen.getByText("decision:")).toBeInTheDocument();
    expect(screen.getByText("AUTO_RECOVERED")).toBeInTheDocument();
    // Pipeline honesty footer.
    expect(
      screen.getByText(
        /Every scenario invokes the actual pipeline \(policy → safety gate → executor → sandbox provider → verification\)\./
      )
    ).toBeInTheDocument();
    // Run buttons for SYSTEM.
    expect(screen.getByTestId("chaos-run-LATE_SETTLEMENT")).toBeInTheDocument();
  });

  it("SUPPORT sees the catalog but no Run buttons", async () => {
    mockCatalog();
    renderChaos({ role: "SUPPORT", keyName: "support-key" });

    expect(await screen.findByTestId("chaos-card-LATE_SETTLEMENT")).toBeInTheDocument();
    expect(screen.queryByTestId("chaos-run-LATE_SETTLEMENT")).not.toBeInTheDocument();
    expect(screen.getByText(/requires the SYSTEM or ADMIN role/)).toBeInTheDocument();
  });

  it("blocks CUSTOMER at the RoleGate", async () => {
    mockCatalog();
    renderChaos({ role: "CUSTOMER", keyName: "cust-key" });

    expect(await screen.findByText("Access denied")).toBeInTheDocument();
    expect(screen.queryByText("LATE_SETTLEMENT")).not.toBeInTheDocument();
  });

  it("SYSTEM runs LATE_SETTLEMENT and sees the PASS verdict plus invariants checklist", async () => {
    mockCatalog();
    mockRunChaosScenario.mockResolvedValue({
      scenario: "LATE_SETTLEMENT",
      transaction_id: "DEMO-S5",
      verdict: "PASS",
      outcome: {
        decision: "AUTO_RECOVERED",
        status: "VERIFIED",
        blocked_reason: null,
        recovery_id: "rec-1",
        provider_reference: "REL-1",
      },
      invariants: [
        { name: "sandbox_ledger_balanced", held: true, detail: "held 10000" },
        { name: "no_double_release", held: true, detail: "one release" },
      ],
      note: null,
    } satisfies ChaosRunResult);
    renderChaos({ role: "SYSTEM", keyName: "system-key" });

    await userEvent.click(await screen.findByTestId("chaos-run-LATE_SETTLEMENT"));

    await waitFor(() => {
      expect(mockRunChaosScenario).toHaveBeenCalledWith("LATE_SETTLEMENT");
    });
    const result = await screen.findByTestId("chaos-result-LATE_SETTLEMENT");
    expect(result).toHaveTextContent("Pass");
    expect(result).toHaveTextContent("AUTO_RECOVERED / VERIFIED");
    expect(screen.getByText("sandbox_ledger_balanced")).toBeInTheDocument();
    expect(screen.getByText("no_double_release")).toBeInTheDocument();
  });

  it("renders the SKIP verdict with its note verbatim", async () => {
    mockCatalog();
    mockRunChaosScenario.mockResolvedValue({
      scenario: "DB_FAILURE_SIMULATION",
      transaction_id: null,
      verdict: "SKIP",
      outcome: null,
      invariants: [],
      note: "DB_FAILURE_SIMULATION not exercised: simulated DB outage unavailable in this build.",
    } satisfies ChaosRunResult);
    renderChaos({ role: "SYSTEM", keyName: "system-key" });

    await userEvent.click(await screen.findByTestId("chaos-run-DB_FAILURE_SIMULATION"));

    const result = await screen.findByTestId("chaos-result-DB_FAILURE_SIMULATION");
    expect(result).toHaveTextContent("Skipped");
    expect(result).toHaveTextContent(
      "DB_FAILURE_SIMULATION not exercised: simulated DB outage unavailable in this build."
    );
  });

  it("renders a FAIL verdict with a red not-held invariant", async () => {
    mockCatalog();
    mockRunChaosScenario.mockResolvedValue({
      scenario: "LATE_SETTLEMENT",
      transaction_id: "DEMO-S5",
      verdict: "FAIL",
      outcome: {
        decision: "AUTO_RECOVERED",
        status: "VERIFIED",
        blocked_reason: null,
        recovery_id: "rec-2",
        provider_reference: "REL-2",
      },
      invariants: [{ name: "sandbox_ledger_balanced", held: false, detail: "off by 100" }],
      note: null,
    } satisfies ChaosRunResult);
    renderChaos({ role: "SYSTEM", keyName: "system-key" });

    await userEvent.click(await screen.findByTestId("chaos-run-LATE_SETTLEMENT"));

    const result = await screen.findByTestId("chaos-result-LATE_SETTLEMENT");
    expect(result).toHaveTextContent("Fail");
    const inv = screen.getByText("sandbox_ledger_balanced");
    expect(inv).toHaveClass("text-red-700");
  });

  it("renders an honest error when the catalog fails", async () => {
    mockGetChaosScenarios.mockRejectedValue(new Error("catalog down"));
    renderChaos({ role: "SYSTEM", keyName: "system-key" });

    expect(await screen.findByText("Scenarios unavailable")).toBeInTheDocument();
    expect(screen.getByText("catalog down")).toBeInTheDocument();
  });
});
