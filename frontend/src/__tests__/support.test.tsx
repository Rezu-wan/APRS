import "../test/mocks";

import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import SupportOverview from "../pages/support/SupportOverview";
import CustomerProfile from "../pages/support/CustomerProfile";
import SupportTransaction from "../pages/support/SupportTransaction";
import { AppRoutes } from "../router";
import {
  makeSupportCustomerProfile,
  makeCustomerSearchResult,
  makeSupportCase,
  makeSupportOverview,
  makeTimeline,
  makeTransaction,
} from "../test/factories";
import {
  apiError,
  mockGetSupportCases,
  mockGetSupportCustomerProfile,
  mockGetSupportOverview,
  mockGetTimeline,
  mockGetTransaction,
  mockCreateSupportCase,
  mockSearchSupportCustomers,
  mockUpdateSupportCase,
  resetApiMocks,
} from "../test/mocks";
import { renderWithProviders } from "../test/utils";

afterEach(() => cleanup());
beforeEach(() => {
  resetApiMocks();
  sessionStorage.clear();
});

function renderOverview() {
  return renderWithProviders(
    <Routes>
      <Route path="/support" element={<SupportOverview />} />
      <Route path="/support/customers/:customerId" element={<div>Profile page reached</div>} />
      <Route path="/support/transactions/:transactionId" element={<div>Transaction page reached</div>} />
    </Routes>,
    { route: "/support", authUser: { role: "SUPPORT", keyName: "k" } }
  );
}

describe("SupportOverview", () => {
  it("renders real queue counts, the needs-attention row, and an honest live-off indicator", async () => {
    mockGetSupportOverview.mockResolvedValue(makeSupportOverview());

    renderOverview();

    // queue counts from cases_by_status (human labels)
    const queue = await screen.findByTestId("live-indicator");
    expect(queue).toHaveTextContent("Live updates off"); // ticket mock rejects — honest state
    expect(await screen.findByText("Open")).toBeInTheDocument();
    expect(screen.getByText("Escalated")).toBeInTheDocument();

    // needs-attention row from real recovery evidence
    expect(await screen.findByText("TXN-BLOCKED-1")).toBeInTheDocument();
    expect(screen.getByTestId("blocked-reason")).toHaveAttribute(
      "title",
      "DOUBLE_DEDUCTION"
    );
  });

  it("shows honest empty states when the queue has nothing to do", async () => {
    mockGetSupportOverview.mockResolvedValue(makeSupportOverview({ needs_attention: [], recent_activity: [] }));

    renderOverview();

    expect(await screen.findByText("No recoveries need attention")).toBeInTheDocument();
    expect(screen.getByText("No recent activity")).toBeInTheDocument();
  });

  it("surfaces overview failures with a retry", async () => {
    mockGetSupportOverview.mockRejectedValue(apiError(500, "INTERNAL_ERROR", "db down"));

    renderOverview();

    expect(await screen.findByText("db down")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(mockGetSupportOverview.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("searches customers and navigates to the profile on open", async () => {
    mockGetSupportOverview.mockResolvedValue(makeSupportOverview());
    mockSearchSupportCustomers.mockResolvedValue({
      query: "nur",
      results: [makeCustomerSearchResult()],
      total: 1,
    });

    renderOverview();

    await userEvent.type(await screen.findByLabelText("Search customers"), "nur");
    await userEvent.click(screen.getByRole("button", { name: /search/i }));

    expect(await screen.findByText("Nur Rahim")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /open profile/i }));

    expect(await screen.findByText("Profile page reached")).toBeInTheDocument();
  });

  it("shows the not-in-registry badge for dataset_unknown customers", async () => {
    mockGetSupportOverview.mockResolvedValue(makeSupportOverview());
    mockSearchSupportCustomers.mockResolvedValue({
      query: "USER-ONLY",
      results: [
        makeCustomerSearchResult({
          customer_id: "USER-ONLY-1",
          full_name: "USER-ONLY-1",
          email: "",
          phone: null,
          status: "unknown",
          segment: null,
          risk_profile: null,
          country: null,
          dataset_known: false,
        }),
      ],
      total: 1,
    });

    renderOverview();

    await userEvent.type(await screen.findByLabelText("Search customers"), "USER-ONLY");
    await userEvent.click(screen.getByRole("button", { name: /search/i }));

    expect(await screen.findByTestId("not-in-registry")).toBeInTheDocument();
  });

  it("renders an honest no-results empty state", async () => {
    mockGetSupportOverview.mockResolvedValue(makeSupportOverview());
    mockSearchSupportCustomers.mockResolvedValue({ query: "zzz", results: [], total: 0 });

    renderOverview();

    await userEvent.type(await screen.findByLabelText("Search customers"), "zzz");
    await userEvent.click(screen.getByRole("button", { name: /search/i }));

    expect(await screen.findByText("No customers found")).toBeInTheDocument();
  });
});

describe("CustomerProfile", () => {
  function renderProfile(customerId = "CUST-SUP-1") {
    return renderWithProviders(
      <Routes>
        <Route path="/support/customers/:customerId" element={<CustomerProfile />} />
        <Route path="/support/transactions/:transactionId" element={<div>Transaction page reached</div>} />
      </Routes>,
      { route: `/support/customers/${customerId}`, authUser: { role: "SUPPORT", keyName: "k" } }
    );
  }

  it("renders the registry customer with accounts and aggregates", async () => {
    mockGetSupportCustomerProfile.mockResolvedValue(makeSupportCustomerProfile());

    renderProfile();

    expect(await screen.findByText("Nur Rahim")).toBeInTheDocument();
    expect(screen.getByText("nur.rahim@example.com")).toBeInTheDocument();
    expect(screen.getByText("ACCT-SUP-1")).toBeInTheDocument();
    expect(screen.getByText("Profile views are audited.")).toBeInTheDocument();
    // aggregate chips
    expect(screen.getByText("12")).toBeInTheDocument(); // transactions
    expect(screen.queryByTestId("not-in-registry")).not.toBeInTheDocument();
  });

  it("flags customers that exist only as transaction users", async () => {
    mockGetSupportCustomerProfile.mockResolvedValue(
      makeSupportCustomerProfile({
        customer: {
          customer_id: "USER-ONLY-9",
          full_name: "USER-ONLY-9",
          email: "",
          phone: null,
          status: "unknown",
          segment: null,
          risk_profile: null,
          country: null,
        },
        dataset_known: false,
        accounts: [],
      })
    );

    renderProfile("USER-ONLY-9");

    expect(await screen.findByTestId("not-in-registry")).toBeInTheDocument();
  });

  it("renders the honest 404 state for unknown customers", async () => {
    mockGetSupportCustomerProfile.mockRejectedValue(apiError(404, "NOT_FOUND", "no such customer"));

    renderProfile("NOPE-123");

    expect(await screen.findByText("No customer found")).toBeInTheDocument();
    expect(screen.getByText(/NOPE-123/)).toBeInTheDocument();
  });
});

describe("SupportTransaction case workflow", () => {
  const tx = makeTransaction({ transaction_id: "TXN-1", user_id: "CUST-SUP-1" });

  function renderTx(transactionId = "TXN-1") {
    return renderWithProviders(
      <Routes>
        <Route path="/support/transactions/:transactionId" element={<SupportTransaction />} />
        <Route path="/support/customers/:customerId" element={<div>Profile page reached</div>} />
      </Routes>,
      { route: `/support/transactions/${transactionId}`, authUser: { role: "SUPPORT", keyName: "k" } }
    );
  }

  function stubEvidence() {
    mockGetTransaction.mockResolvedValue(tx);
    mockGetTimeline.mockResolvedValue(makeTimeline(1));
  }

  it("offers exactly the legal next statuses for an OPEN case", async () => {
    stubEvidence();
    mockGetSupportCases.mockResolvedValue({
      cases: [makeSupportCase({ status: "OPEN" })],
      total: 1,
    });

    renderTx();

    const card = await screen.findByTestId("case-card");
    for (const label of ["Mark In progress", "Mark Waiting for customer", "Mark Escalated", "Mark Resolved", "Mark Closed"]) {
      expect(within(card).getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("sends the chosen transition to the backend", async () => {
    stubEvidence();
    mockGetSupportCases.mockResolvedValue({
      cases: [makeSupportCase({ status: "OPEN" })],
      total: 1,
    });
    mockUpdateSupportCase.mockResolvedValue(makeSupportCase({ status: "IN_PROGRESS" }));

    renderTx();

    const card = await screen.findByTestId("case-card");
    await userEvent.click(within(card).getByRole("button", { name: "Mark In progress" }));

    await waitFor(() =>
      expect(mockUpdateSupportCase).toHaveBeenCalledWith(
        "CASE-000000000001",
        expect.objectContaining({ status: "IN_PROGRESS" })
      )
    );
  });

  it("surfaces an illegal-transition 400 inline instead of swallowing it", async () => {
    stubEvidence();
    mockGetSupportCases.mockResolvedValue({
      cases: [makeSupportCase({ status: "OPEN" })],
      total: 1,
    });
    mockUpdateSupportCase.mockRejectedValue(
      apiError(400, "INVALID_STATE_TRANSITION", "illegal case transition: OPEN -> CLOSED")
    );

    renderTx();

    const card = await screen.findByTestId("case-card");
    await userEvent.click(within(card).getByRole("button", { name: "Mark Closed" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "illegal case transition: OPEN -> CLOSED"
    );
  });

  it("renders a CLOSED case read-only with no transition buttons", async () => {
    stubEvidence();
    mockGetSupportCases.mockResolvedValue({
      cases: [makeSupportCase({ status: "CLOSED", resolved_at: null })],
      total: 1,
    });

    renderTx();

    const card = await screen.findByTestId("case-card");
    await waitFor(() =>
      expect(within(card).queryByRole("button", { name: /^Mark /i })).not.toBeInTheDocument()
    );
    // the closed case moves into the collapsed list
    expect(within(card).getByText("Closed")).toBeInTheDocument();
  });

  it("offers the new-case form when no case exists and sends the payload", async () => {
    stubEvidence();
    mockGetSupportCases.mockResolvedValue({ cases: [], total: 0 });
    mockCreateSupportCase.mockResolvedValue(makeSupportCase());

    renderTx();

    await userEvent.type(await screen.findByLabelText("Subject"), "Customer reports a failed payment");
    await userEvent.click(screen.getByRole("button", { name: /^Open a case$/i }));

    await waitFor(() =>
      expect(mockCreateSupportCase).toHaveBeenCalledWith(
        expect.objectContaining({
          transaction_id: "TXN-1",
          subject: "Customer reports a failed payment",
          priority: "MEDIUM",
        })
      )
    );
  });

  it("links to the customer profile from the context strip", async () => {
    stubEvidence();
    mockGetSupportCases.mockResolvedValue({ cases: [], total: 0 });

    renderTx();

    expect(await screen.findByTestId("customer-link")).toHaveTextContent("CUST-SUP-1");
    await userEvent.click(screen.getByTestId("customer-link"));
    expect(await screen.findByText("Profile page reached")).toBeInTheDocument();
  });
});

describe("Support route gate", () => {
  it("denies CUSTOMER at /support", async () => {
    renderWithProviders(<AppRoutes />, {
      route: "/support",
      authUser: { role: "CUSTOMER", keyName: "k" },
    });

    expect(await screen.findByText("Access denied")).toBeInTheDocument();
  });
});
