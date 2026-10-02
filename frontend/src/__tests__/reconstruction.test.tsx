import "../test/mocks";

import { cleanup, screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import TransactionDetails from "../pages/TransactionDetails";
import { makeReconstruction, makeTimeline, makeTransaction } from "../test/factories";
import {
  apiError,
  mockGetReconstruction,
  mockGetTimeline,
  mockGetTransaction,
  neverPromise,
  resetApiMocks,
} from "../test/mocks";
import { renderWithProviders } from "../test/utils";

afterEach(() => cleanup());
beforeEach(() => {
  resetApiMocks();
  sessionStorage.clear();
});

function renderDetails(transactionId: string, role: "ADMIN" | "CUSTOMER" = "ADMIN") {
  return renderWithProviders(
    <Routes>
      <Route path="/transactions/:transactionId" element={<TransactionDetails />} />
    </Routes>,
    { route: `/transactions/${transactionId}`, authUser: { role, keyName: "k" } }
  );
}

function setupPageFixture() {
  mockGetTransaction.mockResolvedValue(makeTransaction());
  mockGetTimeline.mockResolvedValue(makeTimeline(1));
}

describe("ReconstructionPanel", () => {
  it("renders the 4 stage rows with correct statuses for a merchant-timeout reconstruction", async () => {
    setupPageFixture();
    mockGetReconstruction.mockResolvedValue(makeReconstruction());

    renderDetails("TXN-1");

    expect(
      await screen.findByRole("heading", { name: /payment flow reconstruction/i })
    ).toBeInTheDocument();

    // Stage rows (humanized labels + status text). Stage names also appear in
    // the root-cause "last successful / failed stage" lines, hence getAllBy.
    expect(await screen.findByText("Bank debit")).toBeInTheDocument();
    expect(screen.getAllByText("Gateway").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Merchant confirmation").length).toBeGreaterThan(0);
    expect(screen.getByText("Settlement")).toBeInTheDocument();

    // Statuses appear in the checklist (text + icon, never color-only)
    // Statuses appear in the checklist AND the raw-events table, hence getAllBy.
    expect(screen.getAllByText("Confirmed").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("Timeout").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("Not confirmed").length).toBeGreaterThanOrEqual(2);
  });

  it("shows the humanized root cause, stage lines, confidence and evidence lines", async () => {
    setupPageFixture();
    mockGetReconstruction.mockResolvedValue(makeReconstruction());

    renderDetails("TXN-1");

    expect(await screen.findByText("Merchant confirmation timeout")).toBeInTheDocument();
    expect(screen.getByText(/Last successful stage:/)).toBeInTheDocument();
    expect(screen.getByText(/Failed stage:/)).toBeInTheDocument();
    // 0.71 -> 71% with an "evidence coverage" caption
    expect(screen.getByText("71%")).toBeInTheDocument();
    expect(screen.getByText(/Evidence coverage:/)).toBeInTheDocument();
    expect(
      screen.getByText("Merchant confirmation timed out after 30,000 ms.")
    ).toBeInTheDocument();
    expect(
      screen.getByText("Customer bank debit confirmed after 210 ms.")
    ).toBeInTheDocument();
  });

  it('shows "None — completed successfully" for a fully confirmed reconstruction', async () => {
    setupPageFixture();
    mockGetReconstruction.mockResolvedValue(
      makeReconstruction({
        current_stage: "SETTLEMENT",
        last_successful_stage: "SETTLEMENT",
        failure_stage: "UNAVAILABLE",
        root_cause: "NONE",
        customer_debit_status: "CONFIRMED",
        gateway_status: "CONFIRMED",
        merchant_confirmation_status: "CONFIRMED",
        settlement_status: "CONFIRMED",
        reconstruction_confidence: 1,
        missing_events: [],
        evidence_summary: [
          "Customer bank debit confirmed.",
          "Gateway authorization confirmed.",
          "Merchant confirmation confirmed.",
          "Settlement confirmed.",
        ],
      })
    );

    renderDetails("TXN-1");

    expect(await screen.findByText("None — completed successfully")).toBeInTheDocument();
  });

  it("shows a compact empty line on 404 instead of an error banner", async () => {
    setupPageFixture();
    mockGetReconstruction.mockRejectedValue(apiError(404, "NOT_FOUND", "Transaction not found"));

    renderDetails("TXN-1");

    expect(await screen.findByText(/no reconstruction available/i)).toBeInTheDocument();
    // The panel's own error state must not render a retry alert.
    expect(screen.queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
  });

  it('shows the role-restricted line on 403 for CUSTOMER', async () => {
    setupPageFixture();
    mockGetReconstruction.mockRejectedValue(apiError(403, "FORBIDDEN", "Insufficient role"));

    renderDetails("TXN-1", "CUSTOMER");

    expect(
      await screen.findByText(/reconstruction unavailable for your role/i)
    ).toBeInTheDocument();
  });

  it("shows a loading skeleton before content arrives", async () => {
    setupPageFixture();
    mockGetReconstruction.mockReturnValue(neverPromise());

    const { container } = renderDetails("TXN-1");

    await waitFor(() => {
      expect(container.querySelector(".animate-pulse")).toBeInTheDocument();
    });
    expect(screen.queryByText("Bank debit")).not.toBeInTheDocument();
  });

  it("renders the raw events table inside the collapsed details element", async () => {
    setupPageFixture();
    mockGetReconstruction.mockResolvedValue(makeReconstruction());

    renderDetails("TXN-1");

    const details = await screen.findByText(/^Raw events \(/i);
    // <details> is closed by default: the table content is hidden.
    expect(details).toBeInTheDocument();
  });
});
