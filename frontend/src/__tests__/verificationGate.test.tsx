// Stage 10 — SafetyGateCard (APPROVED / BLOCKED / not evaluated) and
// VerificationCard (VERIFIED checklist vs muted blocked variant).

import "../test/mocks";

import { cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { SafetyGateCard } from "../components/safety/SafetyGateCard";
import { VerificationCard } from "../components/recovery/VerificationCard";
import {
  makeReconstruction,
  makeRecovery,
  makeRiskAssessmentResponse,
  makeTransaction,
} from "../test/factories";
import {
  mockGetReconstruction,
  mockGetRecovery,
  mockGetRiskAssessment,
  mockGetTransaction,
  resetApiMocks,
} from "../test/mocks";
import { renderWithProviders } from "../test/utils";

afterEach(() => cleanup());
beforeEach(() => {
  resetApiMocks();
  mockGetTransaction.mockResolvedValue(makeTransaction());
  mockGetReconstruction.mockResolvedValue(makeReconstruction());
  mockGetRiskAssessment.mockResolvedValue(makeRiskAssessmentResponse());
});

describe("SafetyGateCard", () => {
  it("shows APPROVED with all passing rows for a verified recovery", async () => {
    mockGetRecovery.mockResolvedValue(makeRecovery({ status: "VERIFIED" }));

    renderWithProviders(<SafetyGateCard transactionId="TXN-1" />, {
      authUser: { role: "ADMIN", keyName: "k" },
    });

    expect(await screen.findByText("APPROVED")).toBeInTheDocument();
    const card = screen.getByTestId("safety-gate-card");
    expect(card).toHaveClass("judge-enlarge");

    for (const label of [
      "Transaction not already successful",
      "Settlement not confirmed",
      "Single debit",
      "Risk policy permits recovery",
      "Risk level acceptable",
      "Evidence sufficient",
      "Idempotency key bound",
      "Provider state valid",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }

    // No unknown "—" rows in this fixture.
    expect(screen.queryByText("—")).not.toBeInTheDocument();
  });

  it("shows BLOCKED with the humanized reason for a blocked recovery", async () => {
    mockGetRiskAssessment.mockResolvedValue(
      makeRiskAssessmentResponse({}, { anomaly_type: "DOUBLE_DEDUCTION" }),
    );
    mockGetRecovery.mockResolvedValue(
      makeRecovery({
        status: "BLOCKED",
        action: "NO_ACTION",
        blocked_reason: "DOUBLE_DEDUCTION",
        provider_reference: null,
        released_amount: null,
        verified_at: null,
      }),
    );

    renderWithProviders(<SafetyGateCard transactionId="TXN-1" />, {
      authUser: { role: "ADMIN", keyName: "k" },
    });

    expect(await screen.findByText(/^BLOCKED — Multiple debit evidence detected/)).toBeInTheDocument();
    // The double-deduction row must fail, not pass.
    const row = screen.getByText("Single debit").closest("li");
    expect(row?.querySelector("svg.text-red-700")).not.toBeNull();
  });

  it("shows NOT YET EVALUATED and muted unknown rows when no recovery exists", async () => {
    mockGetRecovery.mockResolvedValue(null);

    renderWithProviders(<SafetyGateCard transactionId="TXN-1" />, {
      authUser: { role: "ADMIN", keyName: "k" },
    });

    expect(await screen.findByText("NOT YET EVALUATED")).toBeInTheDocument();
    // Idempotency + provider rows are unknown before processing — rendered as "—".
    const unknownMarkers = screen.getAllByText("—");
    expect(unknownMarkers.length).toBeGreaterThanOrEqual(2);
  });
});

describe("VerificationCard", () => {
  it("renders the 5-row checklist with all ✓ and the VERIFIED chip for a verified record", async () => {
    mockGetRecovery.mockResolvedValue(makeRecovery({ status: "VERIFIED" }));

    renderWithProviders(<VerificationCard transactionId="TXN-1" />, {
      authUser: { role: "ADMIN", keyName: "k" },
    });

    const card = await screen.findByTestId("verification-card");
    expect(card).toHaveClass("judge-enlarge");

    expect(await screen.findByText("VERIFIED")).toBeInTheDocument();
    for (const label of [
      "Amount matched",
      "Reference matched",
      "Transaction state",
      "No post-execution settlement conflict",
      "Single recovery row",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    // 5 passing rows -> 5 emerald check icons.
    expect(card.querySelectorAll("svg.text-emerald-700").length).toBe(5);
    expect(screen.queryByText("NOT VERIFIED")).not.toBeInTheDocument();
    expect(
      screen.getByText(/Post-execution verification must pass/),
    ).toBeInTheDocument();
  });

  it("renders the muted NOT VERIFIED variant for a blocked record", async () => {
    mockGetRecovery.mockResolvedValue(
      makeRecovery({
        status: "BLOCKED",
        action: "NO_ACTION",
        blocked_reason: "DOUBLE_DEDUCTION",
        provider_reference: null,
        released_amount: null,
        verified_at: null,
      }),
    );

    renderWithProviders(<VerificationCard transactionId="TXN-1" />, {
      authUser: { role: "ADMIN", keyName: "k" },
    });

    expect(await screen.findByText("NOT VERIFIED")).toBeInTheDocument();
    const card = screen.getByTestId("verification-card");
    expect(card.querySelectorAll("svg.text-emerald-700").length).toBe(0);
    expect(screen.queryByText("VERIFIED")).not.toBeInTheDocument();
  });

  it("renders an honest muted line when no recovery exists", async () => {
    mockGetRecovery.mockResolvedValue(null);

    renderWithProviders(<VerificationCard transactionId="TXN-1" />, {
      authUser: { role: "ADMIN", keyName: "k" },
    });

    expect(
      await screen.findByText(/No recovery has run yet — there is nothing to verify/),
    ).toBeInTheDocument();
  });
});
