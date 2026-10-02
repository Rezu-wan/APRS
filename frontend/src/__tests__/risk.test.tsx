import "../test/mocks";

import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { RiskAssessmentPanel } from "../components/risk/RiskAssessmentPanel";
import { makeRiskAssessmentResponse } from "../test/factories";
import {
  apiError,
  mockGetRiskAssessment,
  mockRunRiskAssessment,
  neverPromise,
  resetApiMocks,
} from "../test/mocks";
import { renderWithProviders } from "../test/utils";

afterEach(() => cleanup());
beforeEach(() => {
  resetApiMocks();
  sessionStorage.clear();
});

function renderPanel(role: "ADMIN" | "SYSTEM" | "CUSTOMER" = "ADMIN") {
  return renderWithProviders(<RiskAssessmentPanel transactionId="TXN-1" />, {
    authUser: { role, keyName: "k" },
  });
}

describe("RiskAssessmentPanel", () => {
  it("renders anomaly chip, risk level, scores, evidence, rules and eligibility", async () => {
    mockGetRiskAssessment.mockResolvedValue(makeRiskAssessmentResponse());

    renderPanel("ADMIN");

    expect(
      await screen.findByRole("heading", { name: /risk assessment/i })
    ).toBeInTheDocument();
    // The query is async — wait for the anomaly chip rather than a sync getByText.
    expect(await screen.findByText("Genuine failure")).toBeInTheDocument();
    // "Low" appears as both the risk level and a severity label.
    expect(screen.getAllByText("Low").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("0.14")).toBeInTheDocument();
    expect(screen.getByText("0.09")).toBeInTheDocument();
    expect(screen.getByText("0.20")).toBeInTheDocument();
    expect(
      screen.getByText("Gateway authorization timed out after 30,000 ms.")
    ).toBeInTheDocument();
    expect(screen.getAllByText("SIMULATOR").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("R1")).toBeInTheDocument();
    expect(screen.getByText("Genuine failure detected")).toBeInTheDocument();
    expect(
      screen.getByText("Eligible — pending recovery decision (not automated)")
    ).toBeInTheDocument();
  });

  it("shows the block reason when the transaction is not a recovery candidate", async () => {
    mockGetRiskAssessment.mockResolvedValue(
      makeRiskAssessmentResponse(
        {},
        {
          recovery_candidate: false,
          recovery_block_reason: "Duplicate customer debit suspected",
          anomaly_type: "DOUBLE_DEDUCTION",
          risk_level: "HIGH",
        }
      )
    );

    renderPanel("ADMIN");

    expect(await screen.findByText("Not eligible")).toBeInTheDocument();
    expect(screen.getByText(/Duplicate customer debit suspected/)).toBeInTheDocument();
    expect(
      screen.queryByText(/pending recovery decision/i)
    ).not.toBeInTheDocument();
  });

  it("shows a muted empty line plus a Run assessment button for ADMIN, and runs it on click", async () => {
    const user = userEvent.setup();
    // First fetch: no assessment. After the mutation invalidates the query,
    // the refetch returns the freshly created assessment.
    mockGetRiskAssessment.mockResolvedValueOnce(null);
    mockGetRiskAssessment.mockResolvedValue(makeRiskAssessmentResponse());
    mockRunRiskAssessment.mockResolvedValue(makeRiskAssessmentResponse());

    renderPanel("ADMIN");

    expect(await screen.findByText(/no risk assessment yet/i)).toBeInTheDocument();
    const runButton = screen.getByRole("button", { name: /run assessment/i });
    await user.click(runButton);

    await waitFor(() => {
      expect(mockRunRiskAssessment).toHaveBeenCalledWith("TXN-1", false);
    });
    // After the mutation succeeds, the panel shows the fresh assessment.
    expect(await screen.findByText("Genuine failure")).toBeInTheDocument();
  });

  it("shows the role-restricted line on 403 for CUSTOMER", async () => {
    mockGetRiskAssessment.mockRejectedValue(apiError(403, "FORBIDDEN", "Insufficient role"));

    renderPanel("CUSTOMER");

    expect(
      await screen.findByText(/risk assessment unavailable for your role/i)
    ).toBeInTheDocument();
  });

  it("renders FALSE_COMPLAINT with softened wording and never the word fraud", async () => {
    mockGetRiskAssessment.mockResolvedValue(
      makeRiskAssessmentResponse(
        {},
        { anomaly_type: "FALSE_COMPLAINT", risk_level: "MEDIUM" }
      )
    );

    const { container } = renderPanel("ADMIN");

    expect(await screen.findByText("Possible false complaint")).toBeInTheDocument();
    expect(container.textContent?.toLowerCase()).not.toContain("fraud");
  });

  it("shows a loading skeleton before content arrives", async () => {
    mockGetRiskAssessment.mockReturnValue(neverPromise());

    const { container } = renderPanel("ADMIN");

    await waitFor(() => {
      expect(container.querySelector(".animate-pulse")).toBeInTheDocument();
    });
    expect(screen.queryByText("Genuine failure")).not.toBeInTheDocument();
  });
});
