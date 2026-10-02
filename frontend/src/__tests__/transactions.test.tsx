import "../test/mocks";

import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import TransactionDetails from "../pages/TransactionDetails";
import TransactionSearch from "../pages/TransactionSearch";
import { makeTimeline, makeTransaction } from "../test/factories";
import {
  apiError,
  mockGetStats,
  mockGetTimeline,
  mockGetTransaction,
  resetApiMocks,
} from "../test/mocks";
import { renderWithProviders } from "../test/utils";

afterEach(() => cleanup());
beforeEach(() => {
  resetApiMocks();
  sessionStorage.clear();
});

describe("TransactionSearch", () => {
  it("navigates to /transactions/:id when an ID is submitted", async () => {
    mockGetTransaction.mockResolvedValue(makeTransaction());
    mockGetTimeline.mockResolvedValue(makeTimeline(1));
    mockGetStats.mockResolvedValue({
      total: 0,
      by_state: {},
      decisions: { LIMIT_RELEASED: 0, MANUAL_REVIEW: 0, RECOVERY_REJECTED: 0 },
    });

    renderWithProviders(
      <Routes>
        <Route path="/transactions" element={<TransactionSearch />} />
        <Route path="/transactions/:transactionId" element={<div>Details page for TXN-1</div>} />
      </Routes>,
      { route: "/transactions", authUser: { role: "ADMIN", keyName: "admin-key" } }
    );

    await userEvent.type(screen.getByLabelText("Transaction ID"), "TXN-1");
    await userEvent.click(screen.getByRole("button", { name: /search/i }));

    expect(await screen.findByText("Details page for TXN-1")).toBeInTheDocument();
  });
});

function renderDetails(transactionId: string, role: "ADMIN" | "CUSTOMER" = "ADMIN") {
  return renderWithProviders(
    <Routes>
      <Route path="/transactions/:transactionId" element={<TransactionDetails />} />
      <Route path="/transactions" element={<div>Back at search</div>} />
    </Routes>,
    { route: `/transactions/${transactionId}`, authUser: { role, keyName: "k" } }
  );
}

describe("TransactionDetails", () => {
  it("shows id, amount + currency and the status badge for a loaded transaction", async () => {
    mockGetTransaction.mockResolvedValue(makeTransaction());
    mockGetTimeline.mockResolvedValue(makeTimeline(2));
    mockGetStats.mockResolvedValue({
      total: 0,
      by_state: {},
      decisions: { LIMIT_RELEASED: 0, MANUAL_REVIEW: 0, RECOVERY_REJECTED: 0 },
    });

    renderDetails("TXN-1");

    expect(await screen.findByText("TXN-1")).toBeInTheDocument();
    // amount formatted with the currency code
    expect(screen.getByText(/1,500\.00/)).toBeInTheDocument();
    expect(screen.getByText(/BDT/)).toBeInTheDocument();
    // status badge (humanized state label from StatusBadge)
    expect(screen.getByText("Recovery pending")).toBeInTheDocument();
  });

  it("shows a friendly not-found UI on a 404", async () => {
    mockGetTransaction.mockRejectedValue(apiError(404, "NOT_FOUND", "Transaction not found"));
    mockGetTimeline.mockRejectedValue(apiError(404, "NOT_FOUND", "Transaction not found"));

    renderDetails("TXN-MISSING");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/not found/i);
    // Should offer a way back to the search page.
    expect(screen.getByRole("link", { name: /back to search/i })).toBeInTheDocument();
  });

  it("shows an error state with a retry button on a 500", async () => {
    mockGetTransaction.mockRejectedValue(apiError(500, "INTERNAL", "The server returned an error."));
    mockGetTimeline.mockRejectedValue(apiError(500, "INTERNAL", "The server returned an error."));

    renderDetails("TXN-1");

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: /try again/i });
    expect(retry).toBeInTheDocument();

    // Retry succeeds on the second attempt.
    mockGetTransaction.mockResolvedValue(makeTransaction());
    mockGetTimeline.mockResolvedValue(makeTimeline(1));
    await userEvent.click(retry);
    expect(await screen.findByText("TXN-1")).toBeInTheDocument();
  });

  it("shows a loading skeleton before content arrives", async () => {
    let resolveTransaction: (value: ReturnType<typeof makeTransaction>) => void = () => {};
    mockGetTransaction.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveTransaction = resolve;
        })
    );
    mockGetTimeline.mockResolvedValue(makeTimeline(0));

    const { container } = renderDetails("TXN-1");

    // Skeleton pulse blocks are visible while pending; no transaction content yet.
    await waitFor(() => {
      expect(container.querySelector(".animate-pulse")).toBeInTheDocument();
    });
    expect(screen.queryByText("TXN-1")).not.toBeInTheDocument();

    resolveTransaction(makeTransaction());
    expect(await screen.findByText("TXN-1")).toBeInTheDocument();
  });

  it("hides the support-only assessment and recovery panels for CUSTOMER", async () => {
    mockGetTransaction.mockResolvedValue(makeTransaction());
    mockGetTimeline.mockResolvedValue(makeTimeline(1));

    renderDetails("TXN-1", "CUSTOMER");

    expect(await screen.findByText("TXN-1")).toBeInTheDocument();
    expect(screen.queryByText("Model assessment")).not.toBeInTheDocument();
    expect(screen.queryByText(/Recovery decision/i)).not.toBeInTheDocument();
    // The explanation card is available to all roles.
    expect(screen.getByRole("heading", { name: /Explanation/i })).toBeInTheDocument();
  });
});
