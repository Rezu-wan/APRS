// AutonomousRecoveryPanel: verified/blocked/failed display, empty state with
// the run action for staff, and the customer-role muted line. Display-only —
// the backend's policy + safety gate own every decision.

import "../test/mocks";

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { AutonomousRecoveryPanel } from "../components/recovery/AutonomousRecoveryPanel";
import { makeRecovery } from "../test/factories";
import {
  mockGetRecovery,
  mockProcessRecovery,
  resetApiMocks,
} from "../test/mocks";
import { renderWithProviders } from "../test/utils";

function renderPanel(authRole: "SYSTEM" | "ADMIN" | "SUPPORT" | "CUSTOMER" = "ADMIN") {
  return renderWithProviders(<AutonomousRecoveryPanel transactionId="TXN-1" />, {
    route: "/transactions/TXN-1",
    authUser: { role: authRole, keyName: "api_key" },
  });
}

beforeEach(() => {
  resetApiMocks();
});

describe("AutonomousRecoveryPanel", () => {
  it("renders a VERIFIED recovery with decision, sandbox chip, reference and verification", async () => {
    mockGetRecovery.mockResolvedValue(makeRecovery());
    renderPanel();

    expect(await screen.findByText("Automatically recovered")).toBeInTheDocument();
    expect(screen.getByText("Verified")).toBeInTheDocument();
    expect(screen.getByText("Release limit")).toBeInTheDocument();
    expect(screen.getByText("Simulated sandbox — no real money moves")).toBeInTheDocument();
    expect(screen.getByText("Simulated Sandbox")).toBeInTheDocument();
    expect(screen.getByText("REL-A1B2C3D4")).toBeInTheDocument();
    expect(screen.getByText(/Passed — released amount matches/)).toBeInTheDocument();
  });

  it("renders a BLOCKED recovery with the blocked reason and no verification pass", async () => {
    mockGetRecovery.mockResolvedValue(
      makeRecovery({
        status: "BLOCKED",
        action: "NO_ACTION",
        decision_reason: "Multiple customer debit confirmations require manual review.",
        blocked_reason: "DOUBLE_DEDUCTION",
        provider_reference: null,
        released_amount: null,
        verified_at: null,
      }),
    );
    renderPanel();

    expect(await screen.findByText("Recovery blocked")).toBeInTheDocument();
    expect(screen.getByText("Blocked")).toBeInTheDocument();
    // Stage 10: structured blocked block (heading + humanized reason + action + provider).
    expect(screen.getByText("RECOVERY BLOCKED")).toBeInTheDocument();
    expect(
      screen.getByText(/Multiple debit evidence detected — releasing funds could double/),
    ).toBeInTheDocument();
    expect(screen.getAllByText("No automatic recovery").length).toBeGreaterThan(0);
    expect(screen.getByText("NOT CALLED")).toBeInTheDocument();
    expect(screen.getByText("Not verified")).toBeInTheDocument();
    expect(screen.queryByText(/Passed — released amount/)).not.toBeInTheDocument();
  });

  it("renders a FAILED recovery with the failure reason", async () => {
    mockGetRecovery.mockResolvedValue(
      makeRecovery({
        status: "FAILED",
        failure_reason: "PROVIDER_TIMEOUT",
        provider_reference: null,
        released_amount: null,
        verified_at: null,
      }),
    );
    renderPanel();

    expect(await screen.findByText("Failed")).toBeInTheDocument();
    expect(screen.getByText(/Failure:/)).toBeInTheDocument();
    expect(screen.getByText("Not verified")).toBeInTheDocument();
  });

  it("shows the empty state with a run button for ADMIN and calls processRecovery", async () => {
    const user = userEvent.setup();
    mockGetRecovery.mockResolvedValue(null);
    mockProcessRecovery.mockResolvedValue(
      (await import("../test/factories")).makeRecoveryOutcome(),
    );
    renderPanel("ADMIN");

    expect(await screen.findByText("No autonomous recovery yet.")).toBeInTheDocument();
    const button = screen.getByRole("button", { name: /run autonomous recovery/i });
    await user.click(button);
    await waitFor(() => expect(mockProcessRecovery).toHaveBeenCalledTimes(1));
  });

  it("hides the run button for SUPPORT but still shows the recovery", async () => {
    mockGetRecovery.mockResolvedValue(makeRecovery());
    renderPanel("SUPPORT");

    expect(await screen.findByText("Verified")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /run autonomous recovery/i }),
    ).not.toBeInTheDocument();
  });

  it("shows the muted role line for CUSTOMER (backend 403)", async () => {
    const { ApiError } = await import("../api/client");
    mockGetRecovery.mockRejectedValue(new ApiError(403, "INSUFFICIENT_PERMISSIONS", "no"));
    renderPanel("CUSTOMER");

    expect(
      await screen.findByText("Autonomous recovery unavailable for your role."),
    ).toBeInTheDocument();
  });
});
