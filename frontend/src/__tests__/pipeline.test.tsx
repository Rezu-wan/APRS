// Stage 10 — RecoveryPipeline: honest step derivation and final-state chip.

import "../test/mocks";

import { cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { RecoveryPipeline } from "../components/pipeline/RecoveryPipeline";
import { makeReconstruction, makeRecovery, makeRiskAssessmentResponse } from "../test/factories";
import {
  mockGetReconstruction,
  mockGetRecovery,
  mockGetRiskAssessment,
  resetApiMocks,
} from "../test/mocks";
import { renderWithProviders } from "../test/utils";

afterEach(() => cleanup());
beforeEach(() => {
  resetApiMocks();
});

function renderPipeline() {
  return renderWithProviders(<RecoveryPipeline transactionId="TXN-1" />, {
    authUser: { role: "ADMIN", keyName: "k" },
  });
}

describe("RecoveryPipeline", () => {
  it("renders 7 steps with all done and the VERIFIED chip on a verified recovery", async () => {
    mockGetReconstruction.mockResolvedValue(makeReconstruction());
    mockGetRiskAssessment.mockResolvedValue(makeRiskAssessmentResponse());
    mockGetRecovery.mockResolvedValue(makeRecovery({ status: "VERIFIED" }));

    renderPipeline();

    const root = await screen.findByTestId("recovery-pipeline");
    expect(root).toHaveClass("judge-enlarge");

    for (const label of [
      "Events",
      "Reconstruction",
      "Risk",
      "Policy",
      "Safety",
      "Executor",
      "Verification",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }

    expect(await screen.findByText("VERIFIED")).toBeInTheDocument();
    expect(screen.queryByText("READY — awaiting recovery")).not.toBeInTheDocument();
    expect(screen.queryByText("BLOCKED")).not.toBeInTheDocument();
  });

  it("renders the READY chip when no recovery row exists", async () => {
    mockGetReconstruction.mockResolvedValue(makeReconstruction());
    mockGetRiskAssessment.mockResolvedValue(makeRiskAssessmentResponse());
    mockGetRecovery.mockResolvedValue(null);

    renderPipeline();

    expect(await screen.findByText("READY — awaiting recovery")).toBeInTheDocument();
    expect(screen.queryByText("VERIFIED")).not.toBeInTheDocument();
  });

  it("renders the BLOCKED chip on a blocked recovery and marks safety/executor blocked", async () => {
    mockGetReconstruction.mockResolvedValue(makeReconstruction());
    mockGetRiskAssessment.mockResolvedValue(makeRiskAssessmentResponse());
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

    renderPipeline();

    expect(await screen.findByText("BLOCKED")).toBeInTheDocument();
    expect(screen.queryByText(/READY/)).not.toBeInTheDocument();

    // Blocked steps render the ✕ icon (red XCircle) inside the step icons.
    const root = screen.getByTestId("recovery-pipeline");
    const redIcons = root.querySelectorAll("svg.text-red-600");
    expect(redIcons.length).toBeGreaterThanOrEqual(2); // Safety + Executor
  });
});
