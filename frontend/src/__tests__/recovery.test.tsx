import "../test/mocks";

import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import TransactionDetails from "../pages/TransactionDetails";
import { makeTimeline, makeTransaction } from "../test/factories";
import {
  apiError,
  mockGetTimeline,
  mockGetTransaction,
  mockReleaseLimit,
  neverPromise,
  resetApiMocks,
} from "../test/mocks";
import { renderWithProviders } from "../test/utils";

afterEach(() => cleanup());
beforeEach(() => {
  resetApiMocks();
  sessionStorage.clear();
  mockGetTimeline.mockResolvedValue(makeTimeline(1));
});

function renderRecoveryDetails() {
  return renderWithProviders(
    <Routes>
      <Route path="/transactions/:transactionId" element={<TransactionDetails />} />
    </Routes>,
    { route: "/transactions/TXN-1", authUser: { role: "ADMIN", keyName: "admin-key" } }
  );
}

async function renderPendingDetails() {
  mockGetTransaction.mockResolvedValue(makeTransaction({ current_state: "RECOVERY_PENDING" }));
  renderRecoveryDetails();
  // Wait for the release button to appear (proves the transaction loaded).
  return await screen.findByRole("button", { name: /release limit/i });
}

describe("RecoveryCard (in TransactionDetails)", () => {
  it("disables the release button while the decision is in flight", async () => {
    mockReleaseLimit.mockReturnValue(neverPromise()); // never resolves — stays in-flight
    const button = await renderPendingDetails();

    expect(button).toBeEnabled();
    await userEvent.click(button);

    // In-flight: button disabled and shows the deciding label, no decision yet.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /deciding/i })).toBeDisabled();
    });
    expect(screen.queryByText(/Decision:/i)).not.toBeInTheDocument();
  });

  it("shows the LIMIT_RELEASED decision with reason when the release succeeds", async () => {
    mockReleaseLimit.mockResolvedValue(
      // makeDecision with already_applied=false and a human-readable reason
      {
        transaction_id: "TXN-1",
        decision: "LIMIT_RELEASED",
        safe_to_release: true,
        safe_to_release_probability: 0.91,
        risk_score: 0.12,
        reason: "Strong repayment history and low risk score.",
        decided_by: "policy:v1",
        decided_at: "2026-10-02T09:58:00Z",
        current_state: "LIMIT_RELEASED",
        already_applied: false,
      }
    );
    const button = await renderPendingDetails();

    await userEvent.click(button);

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Limit released");
    expect(status).toHaveTextContent("Strong repayment history and low risk score.");
    expect(status).not.toHaveTextContent(/already been applied/i);
  });

  it("shows the MANUAL_REVIEW decision and not 'released'", async () => {
    mockReleaseLimit.mockResolvedValue({
      transaction_id: "TXN-1",
      decision: "MANUAL_REVIEW",
      safe_to_release: false,
      safe_to_release_probability: 0.2,
      risk_score: 0.8,
      reason: "Risk score too high for automatic release.",
      decided_by: "policy:v1",
      decided_at: "2026-10-02T09:58:00Z",
      current_state: "MANUAL_REVIEW",
      already_applied: false,
    });
    const button = await renderPendingDetails();

    await userEvent.click(button);

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/manual review/i);
    expect(status).toHaveTextContent("Risk score too high for automatic release.");
    expect(status).not.toHaveTextContent(/released/i);
  });

  it("renders the backend error message on a 409", async () => {
    mockReleaseLimit.mockRejectedValue(
      apiError(409, "NOT_RECOVERABLE", "Transaction is not in a recoverable state.")
    );
    const button = await renderPendingDetails();

    await userEvent.click(button);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Transaction is not in a recoverable state.");
  });

  it("does not render the release button for SUPPORT but still shows assessment info", async () => {
    mockGetTransaction.mockResolvedValue(makeTransaction({ current_state: "RECOVERY_PENDING" }));
    renderWithProviders(
      <Routes>
        <Route path="/transactions/:transactionId" element={<TransactionDetails />} />
      </Routes>,
      { route: "/transactions/TXN-1", authUser: { role: "SUPPORT", keyName: "support-key" } }
    );

    // Assessment panel (RoleGate allows SUPPORT) is visible.
    expect(await screen.findByText("Model assessment")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText(/Recovery decision/i)).toBeInTheDocument();
    });
    // No release button for SUPPORT; a lock note explains why.
    expect(screen.queryByRole("button", { name: /release limit/i })).not.toBeInTheDocument();
    expect(screen.getByText(/Release decisions require/i)).toBeInTheDocument();
  });
});
