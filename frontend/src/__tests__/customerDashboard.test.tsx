import "../test/mocks";

import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import Dashboard from "../pages/Dashboard";
import TransactionSearch from "../pages/TransactionSearch";
import TransactionDetails from "../pages/TransactionDetails";
import {
  makeCustomerProfile,
  makeCustomerReport,
  makeCustomerReportFileResponse,
  makeReconstruction,
  makeTimeline,
  makeTransaction,
  makeTransactionListResponse,
  makeTransactionsSummary,
} from "../test/factories";
import {
  apiError,
  mockFileCustomerReport,
  mockGetCustomerReport,
  mockGetMyProfile,
  mockGetReconstruction,
  mockGetRecovery,
  mockGetRiskAssessment,
  mockGetStats,
  mockGetTimeline,
  mockGetTransaction,
  mockListTransactions,
  mockGetTransactionsSummary,
  resetApiMocks,
} from "../test/mocks";
import { renderWithProviders } from "../test/utils";

afterEach(() => cleanup());
beforeEach(() => {
  resetApiMocks();
  sessionStorage.clear();
});

function renderCustomer(ui: React.ReactNode, { route = "/dashboard" }: { route?: string } = {}) {
  return renderWithProviders(
    <Routes>
      <Route path="/dashboard" element={ui} />
      <Route path="/transactions" element={ui} />
      <Route path="/transactions/:transactionId" element={ui} />
    </Routes>,
    { route, authUser: { role: "CUSTOMER", keyName: "customer-key", customerId: "USER-DEMO" } }
  );
}

describe("CustomerDashboard", () => {
  it("renders the customer's own stats and recent activity — never the staff stats endpoint", async () => {
    mockGetTransactionsSummary.mockResolvedValue(makeTransactionsSummary());
    mockListTransactions.mockResolvedValue(
      makeTransactionListResponse([
        makeTransaction({
          transaction_id: "TXN-A",
          current_state: "RECOVERY_PENDING",
          merchant_id: "MER-0001",
          merchant_name: "Dhaka Fresh Mart",
        }),
        makeTransaction({ transaction_id: "TXN-B", current_state: "SUCCESS" }),
      ])
    );

    renderCustomer(<Dashboard />);

    // Stats come from the role-scoped summary endpoint.
    expect(await screen.findByText("My payments")).toBeInTheDocument();
    expect(await screen.findByText("3")).toBeInTheDocument(); // total
    expect(screen.getByText(/4,200\.00/)).toBeInTheDocument(); // BDT total amount
    // Status breakdown bars show real by_state counts (the labels also appear
    // on recent-activity badges, hence getAllBy).
    expect(screen.getAllByText("Recovery pending").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Success").length).toBeGreaterThanOrEqual(1);
    // Recent activity lists the scoped transactions.
    expect(screen.getByText("TXN-A")).toBeInTheDocument();
    expect(screen.getByText("TXN-B")).toBeInTheDocument();
    // Merchant rows resolve to the catalog name, falling back to the raw id.
    expect(screen.getByText(/Dhaka Fresh Mart/)).toBeInTheDocument();
    expect(screen.getByText(/M-2002/)).toBeInTheDocument();
    // The staff-only platform stats endpoint must NOT be called (it 403s).
    expect(mockGetStats).not.toHaveBeenCalled();
  });

  it("links recent activity entries to the transaction details page", async () => {
    mockGetTransactionsSummary.mockResolvedValue(makeTransactionsSummary());
    mockListTransactions.mockResolvedValue(
      makeTransactionListResponse([makeTransaction({ transaction_id: "TXN-A" })])
    );

    renderWithProviders(
      <Routes>
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/transactions/:transactionId" element={<div>Details for TXN-A</div>} />
      </Routes>,
      { route: "/dashboard", authUser: { role: "CUSTOMER", keyName: "k", customerId: "USER-DEMO" } }
    );

    await userEvent.click(await screen.findByRole("link", { name: /TXN-A/i }));
    expect(await screen.findByText("Details for TXN-A")).toBeInTheDocument();
  });

  it("shows a useful empty state when the customer has no transactions", async () => {
    mockGetTransactionsSummary.mockResolvedValue(
      makeTransactionsSummary({ total: 0, by_state: {}, amounts_by_currency: {} })
    );
    mockListTransactions.mockResolvedValue(makeTransactionListResponse([]));

    renderCustomer(<Dashboard />);

    expect(await screen.findByText("No transactions yet")).toBeInTheDocument();
    expect(screen.queryByText("Recent activity")).not.toBeInTheDocument();
  });

  it("shows an error state with retry when the summary fails", async () => {
    mockGetTransactionsSummary.mockRejectedValue(
      apiError(500, "INTERNAL", "The server returned an error.")
    );

    renderCustomer(<Dashboard />);

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: /try again/i });
    mockGetTransactionsSummary.mockResolvedValue(makeTransactionsSummary());
    await userEvent.click(retry);
    expect(await screen.findByText("My payments")).toBeInTheDocument();
  });

  it("greets the customer by name with segment/archetype chips when the profile loads", async () => {
    mockGetTransactionsSummary.mockResolvedValue(makeTransactionsSummary());
    mockListTransactions.mockResolvedValue(makeTransactionListResponse([]));
    mockGetMyProfile.mockResolvedValue(makeCustomerProfile());

    renderCustomer(<Dashboard />);

    expect(await screen.findByText("Hello, Sabbir Mustafi")).toBeInTheDocument();
    expect(screen.getByText("Retail")).toBeInTheDocument();
    expect(screen.getByText("Biller")).toBeInTheDocument();
    expect(screen.getByText("CUST-000084")).toBeInTheDocument();
    // Non-active status would show; active does not.
    expect(screen.queryByText("Active")).not.toBeInTheDocument();
  });

  it("falls back to the raw account id when the identity has no profile", async () => {
    mockGetTransactionsSummary.mockResolvedValue(makeTransactionsSummary());
    mockListTransactions.mockResolvedValue(makeTransactionListResponse([]));
    // mockGetMyProfile default is null (getMyProfile maps 404 → null)

    renderCustomer(<Dashboard />);

    expect(await screen.findByText("My payments")).toBeInTheDocument();
    expect(screen.getByText(/Account/)).toBeInTheDocument();
  });
});

describe("CustomerTransactionList", () => {
  it("lists the customer's transactions with statuses and amounts", async () => {
    mockListTransactions.mockResolvedValue(
      makeTransactionListResponse([
        makeTransaction({ transaction_id: "TXN-A", current_state: "RECOVERY_PENDING" }),
        makeTransaction({ transaction_id: "TXN-B", current_state: "SUCCESS" }),
      ])
    );

    renderCustomer(<TransactionSearch />, { route: "/transactions" });

    expect(await screen.findByText(/TXN-A/)).toBeInTheDocument();
    expect(screen.getByText(/TXN-B/)).toBeInTheDocument();
    expect(mockListTransactions).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 10, offset: 0 })
    );
  });

  it("shows resolved merchant names and filters by them", async () => {
    mockListTransactions.mockResolvedValue(
      makeTransactionListResponse([
        makeTransaction({
          transaction_id: "TXN-A",
          merchant_id: "MER-0001",
          merchant_name: "Dhaka Fresh Mart",
          merchant_category: "grocery",
        }),
        makeTransaction({
          transaction_id: "TXN-B",
          merchant_id: "",
          merchant_name: null,
          merchant_category: null,
        }),
      ])
    );

    renderCustomer(<TransactionSearch />, { route: "/transactions" });
    expect(await screen.findByText(/Dhaka Fresh Mart/)).toBeInTheDocument();
    expect(screen.getByText(/grocery/)).toBeInTheDocument();
    // Empty merchant id/name → honest "No merchant" placeholder.
    expect(screen.getByText("No merchant")).toBeInTheDocument();

    // The in-page text filter also matches the resolved merchant name.
    await userEvent.type(screen.getByLabelText(/filter by id or merchant/i), "fresh");
    expect(screen.getByText(/TXN-A/)).toBeInTheDocument();
    expect(screen.queryByText(/TXN-B/)).not.toBeInTheDocument();
  });

  it("passes the status filter to the backend", async () => {
    mockListTransactions.mockResolvedValue(
      makeTransactionListResponse([makeTransaction({ transaction_id: "TXN-A" })])
    );

    renderCustomer(<TransactionSearch />, { route: "/transactions" });
    await screen.findByText(/TXN-A/);

    await userEvent.selectOptions(screen.getByLabelText("Status"), "SUCCESS");
    await waitFor(() => {
      expect(mockListTransactions).toHaveBeenLastCalledWith(
        expect.objectContaining({ state: "SUCCESS", offset: 0 })
      );
    });
  });

  it("paginates server-side and disables Previous on the first page", async () => {
    const page = Array.from({ length: 10 }, (_, i) =>
      makeTransaction({ transaction_id: `TXN-${i}` })
    );
    mockListTransactions.mockResolvedValue(makeTransactionListResponse(page, { total: 23 }));

    renderCustomer(<TransactionSearch />, { route: "/transactions" });

    expect(await screen.findByText(/Showing 1–10 of 23/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /previous/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /next/i })).toBeEnabled();

    await userEvent.click(screen.getByRole("button", { name: /next/i }));
    await waitFor(() => {
      expect(mockListTransactions).toHaveBeenLastCalledWith(
        expect.objectContaining({ offset: 10 })
      );
    });
  });

  it("filters the loaded page by ID or merchant text", async () => {
    mockListTransactions.mockResolvedValue(
      makeTransactionListResponse([
        makeTransaction({ transaction_id: "TXN-ALPHA", merchant_id: "MERCHANT-DEMO" }),
        makeTransaction({ transaction_id: "TXN-BETA", merchant_id: "MERCHANT-OTHER" }),
      ])
    );

    renderCustomer(<TransactionSearch />, { route: "/transactions" });
    await screen.findByText(/TXN-ALPHA/);

    await userEvent.type(screen.getByLabelText(/filter by id or merchant/i), "ALPHA");
    expect(screen.getByText(/TXN-ALPHA/)).toBeInTheDocument();
    expect(screen.queryByText(/TXN-BETA/)).not.toBeInTheDocument();
    expect(mockListTransactions).toHaveBeenCalledTimes(1); // text filter is in-page only
  });

  it("distinguishes an empty account from an over-filtered empty result", async () => {
    mockListTransactions.mockResolvedValue(makeTransactionListResponse([]));

    const first = renderCustomer(<TransactionSearch />, { route: "/transactions" });
    expect(await screen.findByText("No transactions yet")).toBeInTheDocument();
    first.unmount();

    mockListTransactions.mockResolvedValue(makeTransactionListResponse([], { total: 0 }));
    renderCustomer(<TransactionSearch />, { route: "/transactions" });
    // With filters active, offer a reset instead of implying the account is empty.
    await userEvent.selectOptions(screen.getByLabelText("Status"), "SUCCESS");
    expect(await screen.findByText("No transactions match")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /clear filters/i })).toBeInTheDocument();
  });
});

describe("CustomerReportCard (inside TransactionDetails)", () => {
  function setupDetails() {
    mockGetTransaction.mockResolvedValue(makeTransaction({ transaction_id: "TXN-1" }));
    mockGetTimeline.mockResolvedValue(makeTimeline(1, { transaction_id: "TXN-1" }));
    mockGetReconstruction.mockResolvedValue(makeReconstruction({ transaction_id: "TXN-1" }));
  }

  function renderDetailsForCustomer() {
    return renderWithProviders(
      <Routes>
        <Route path="/transactions/:transactionId" element={<TransactionDetails />} />
        <Route path="/transactions" element={<div>Back at list</div>} />
      </Routes>,
      { route: "/transactions/TXN-1", authUser: { role: "CUSTOMER", keyName: "k", customerId: "alice" } }
    );
  }

  it("files a report and renders the stored report; staff-only endpoints are never called", async () => {
    setupDetails();
    mockGetCustomerReport.mockResolvedValueOnce(null);
    mockGetCustomerReport.mockResolvedValue(
      makeCustomerReport({ problem_type: "DOUBLE_CHARGED", stage: "SETTLEMENT" })
    );
    mockFileCustomerReport.mockResolvedValue(
      makeCustomerReportFileResponse({
        report: makeCustomerReport({ problem_type: "DOUBLE_CHARGED", stage: "SETTLEMENT" }),
      })
    );

    renderDetailsForCustomer();
    expect(await screen.findByText("TXN-1")).toBeInTheDocument();

    // Customer-allowed reads happen…
    expect(mockGetReconstruction).toHaveBeenCalled();
    // …but the staff-only pipeline reads must NOT fire (they 403 for CUSTOMER).
    expect(mockGetRiskAssessment).not.toHaveBeenCalled();
    expect(mockGetRecovery).not.toHaveBeenCalled();

    // Report form → snake_case payload → stored report view.
    await userEvent.selectOptions(await screen.findByLabelText("What happened?"), "DOUBLE_CHARGED");
    await userEvent.selectOptions(screen.getByLabelText("Where did it happen?"), "SETTLEMENT");
    await userEvent.type(
      screen.getByLabelText(/description/i),
      "I was charged twice for one payment."
    );
    await userEvent.click(screen.getByRole("button", { name: /file report/i }));

    expect(mockFileCustomerReport).toHaveBeenCalledWith(
      expect.objectContaining({
        transactionId: "TXN-1",
        problemType: "DOUBLE_CHARGED",
        stage: "SETTLEMENT",
        description: "I was charged twice for one payment.",
      })
    );
    expect(await screen.findByText("Charged more than once")).toBeInTheDocument();
    // "Settlement" also appears in the reconstruction panel's stage list.
    expect(screen.getAllByText("Settlement").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Open")).toBeInTheDocument();
  });

  it("shows dataset merchant resolution and payment attributes on the details page", async () => {
    setupDetails();
    mockGetTransaction.mockResolvedValue(
      makeTransaction({
        transaction_id: "TXN-1",
        merchant_id: "MER-0001",
        merchant_name: "Dhaka Fresh Mart",
        merchant_category: "grocery",
        transaction_type: "bill_payment",
        channel: "mobile_app",
        direction: "debit",
        country: "BD",
      })
    );

    renderDetailsForCustomer();

    expect(await screen.findByText(/Dhaka Fresh Mart/)).toBeInTheDocument();
    expect(screen.getByText(/grocery/)).toBeInTheDocument();
    expect(screen.getByText(/Bill Payment/)).toBeInTheDocument();
    expect(screen.getByText(/Mobile App/)).toBeInTheDocument();
    expect(screen.getByText("BD")).toBeInTheDocument();
  });

  it("shows an existing report instead of the form", async () => {
    setupDetails();
    mockGetCustomerReport.mockResolvedValue(
      makeCustomerReport({ status: "UNDER_REVIEW", problem_type: "MONEY_NOT_RECEIVED" })
    );

    renderDetailsForCustomer();

    expect(await screen.findByText("Money not received")).toBeInTheDocument();
    expect(screen.getByText("Under review")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /file report/i })).not.toBeInTheDocument();
  });
});

describe("customer vs staff page split", () => {
  it("Dashboard (staff) still uses the platform stats endpoint", async () => {
    mockGetStats.mockResolvedValue({
      total: 0,
      by_state: {},
      decisions: { LIMIT_RELEASED: 0, MANUAL_REVIEW: 0, RECOVERY_REJECTED: 0 },
    });

    renderWithProviders(
      <Routes>
        <Route path="/dashboard" element={<Dashboard />} />
      </Routes>,
      { route: "/dashboard", authUser: { role: "ADMIN", keyName: "admin" } }
    );

    // Empty platform → staff empty state; the staff endpoint was used.
    expect(await screen.findByText("No transactions yet")).toBeInTheDocument();
    expect(mockGetStats).toHaveBeenCalled();
    expect(mockGetTransactionsSummary).not.toHaveBeenCalled();
  });

  it("TransactionSearch (staff) keeps the exact-ID lookup form", async () => {
    renderWithProviders(
      <Routes>
        <Route path="/transactions" element={<TransactionSearch />} />
      </Routes>,
      { route: "/transactions", authUser: { role: "ADMIN", keyName: "admin" } }
    );

    expect(await screen.findByText("Find a transaction")).toBeInTheDocument();
    expect(mockListTransactions).not.toHaveBeenCalled();
  });
});
