// Stage 11 FRONTEND-A — TemporalPanel, BehavioralSignalsPanel, RelationshipsPanel.

import "../test/mocks";

import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import TransactionDetails from "../pages/TransactionDetails";
import { BehavioralSignalsPanel } from "../components/stage11/BehavioralSignalsPanel";
import { RelationshipsPanel } from "../components/stage11/RelationshipsPanel";
import { TemporalPanel } from "../components/stage11/TemporalPanel";
import type {
  BehavioralReport,
  RelationshipResponse,
  TemporalStateReport,
} from "../api/stage11";
import { makeTimeline, makeTransaction } from "../test/factories";
import {
  apiError,
  mockGetBehavioralSignals,
  mockGetRelationships,
  mockGetStateAt,
  mockGetTimeline,
  mockGetTransaction,
  resetApiMocks,
} from "../test/mocks";
import { renderWithProviders } from "../test/utils";

afterEach(() => cleanup());
beforeEach(() => resetApiMocks());

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeTemporalReport(
  overrides: Partial<TemporalStateReport> = {}
): TemporalStateReport {
  return {
    transaction_id: "TXN-1",
    as_of: "2026-10-02T09:45:00+00:00",
    state_then: "PROCESSING",
    last_twin_event_type: "GATEWAY_CONFIRMED",
    observed_event_count: 5,
    excluded_event_count: 1,
    reconstruction: {
      root_cause: "NONE",
      reconstruction_confidence: 0.8,
      stages: {
        bank_debit: "CONFIRMED",
        gateway: "CONFIRMED",
        merchant_confirmation: "NOT_OBSERVED",
        settlement: "NOT_OBSERVED",
      },
      missing_events: [],
    },
    uncertainty_note: "Settlement had not been observed yet at this moment.",
    ...overrides,
  };
}

function makeBehavioralReport(
  overrides: Partial<BehavioralReport> = {}
): BehavioralReport {
  const signal = (i: number, level: BehavioralReport["signals"][number]["level"] = "LOW") => ({
    code: `SIG-${i}`,
    label: `Signal ${i}`,
    value: 1.2,
    unit: "x",
    level,
    description: `Description for signal ${i}.`,
    window: "24h",
    basis: "z_score",
  });
  return {
    transaction_id: "TXN-1",
    signals: [
      signal(1, "HIGH"),
      signal(2, "MEDIUM"),
      signal(3, "UNKNOWN"),
      ...[4, 5, 6, 7, 8, 9].map((i) => signal(i, "LOW")),
    ],
    summary: { high_count: 1, medium_count: 1, unknown_count: 1, overall: "MEDIUM" },
    computed_at: "2026-10-02T10:00:00+00:00",
    feature_version: "behavioral-v1",
    ...overrides,
  };
}

function makeRelationshipsResponse(
  overrides: Partial<RelationshipResponse["report"]> = {}
): RelationshipResponse {
  return {
    report: {
      transaction_id: "TXN-1",
      entities: [
        { type: "user", id: "U-1001" },
        { type: "merchant", id: "M-2002" },
      ],
      edges: [],
      signals: [
        {
          code: "USER_UNUSUAL_COUNT",
          label: "Unusual user volume",
          description: "More transactions than the user's recent baseline.",
          level: "MEDIUM",
          count: 3,
          evidence: ["TXN-A", "TXN-B", "TXN-C", "TXN-D", "TXN-E"],
        },
        {
          code: "MERCHANT_FIRST_SEEN",
          label: "Merchant first seen",
          description: "User had no prior activity with this merchant.",
          level: "LOW",
          count: null,
          evidence: [],
        },
      ],
      computed_at: "2026-10-02T10:00:00+00:00",
      feature_version: "graph-v1",
      ...overrides,
    },
  };
}

// ---------------------------------------------------------------------------
// TemporalPanel
// ---------------------------------------------------------------------------

describe("TemporalPanel", () => {
  it("renders the explainer before a query is made", () => {
    renderWithProviders(<TemporalPanel transactionId="TXN-1" />, {
      authUser: { role: "ADMIN", keyName: "k" },
    });

    expect(
      screen.getByText(/Pick a past moment to see exactly what the system knew then/),
    ).toBeInTheDocument();
    expect(mockGetStateAt.mock.calls.length).toBe(0);
  });

  it("queries on Inspect and renders state_then chip, excluded count and uncertainty note", async () => {
    mockGetStateAt.mockResolvedValue(makeTemporalReport());

    renderWithProviders(<TemporalPanel transactionId="TXN-1" />, {
      authUser: { role: "ADMIN", keyName: "k" },
    });

    await userEvent.type(screen.getByLabelText("Past timestamp to inspect"), "2026-10-02T09:45");
    await userEvent.click(screen.getByRole("button", { name: /inspect state/i }));

    expect(await screen.findByText("Processing")).toBeInTheDocument();
    expect(screen.getByText(/1 later event EXCLUDED from this view/)).toBeInTheDocument();
    expect(
      screen.getByText(/Settlement had not been observed yet at this moment\./),
    ).toBeInTheDocument();
    // Stage checklist renders all 4 stages.
    for (const label of ["Bank debit", "Gateway", "Merchant confirmation", "Settlement"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("renders a muted role line on 403", async () => {
    mockGetStateAt.mockRejectedValue(apiError(403, "INSUFFICIENT_PERMISSIONS", "forbidden"));

    renderWithProviders(<TemporalPanel transactionId="TXN-1" />, {
      authUser: { role: "ADMIN", keyName: "k" },
    });

    await userEvent.type(screen.getByLabelText("Past timestamp to inspect"), "2026-10-02T09:45");
    await userEvent.click(screen.getByRole("button", { name: /inspect state/i }));

    expect(
      await screen.findByText("Temporal queries are available to staff roles."),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// BehavioralSignalsPanel
// ---------------------------------------------------------------------------

describe("BehavioralSignalsPanel", () => {
  it("renders all 9 signal rows with the overall chip", async () => {
    mockGetBehavioralSignals.mockResolvedValue(makeBehavioralReport());

    renderWithProviders(<BehavioralSignalsPanel transactionId="TXN-1" />, {
      authUser: { role: "ADMIN", keyName: "k" },
    });

    expect(await screen.findByText(/1 high · 1 medium · 1 unknown/)).toBeInTheDocument();
    // Overall chip + the one MEDIUM signal row both carry the level text.
    expect(screen.getAllByText("MEDIUM").length).toBe(2);
    expect(screen.getByText("behavioral-v1")).toBeInTheDocument();
    for (let i = 1; i <= 9; i++) {
      expect(screen.getByText(`Signal ${i}`)).toBeInTheDocument();
    }
    // Window · basis tags are rendered per row.
    expect(screen.getAllByText(/24h · z_score/).length).toBe(9);
  });

  it("renders a muted line on 403", async () => {
    mockGetBehavioralSignals.mockRejectedValue(
      apiError(403, "INSUFFICIENT_PERMISSIONS", "forbidden"),
    );

    renderWithProviders(<BehavioralSignalsPanel transactionId="TXN-1" />, {
      authUser: { role: "ADMIN", keyName: "k" },
    });

    expect(
      await screen.findByText("Behavioral signals are available to staff roles."),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// RelationshipsPanel
// ---------------------------------------------------------------------------

describe("RelationshipsPanel", () => {
  it("renders entity chips, signal rows and the advisory footer", async () => {
    mockGetRelationships.mockResolvedValue(makeRelationshipsResponse());

    renderWithProviders(<RelationshipsPanel transactionId="TXN-1" />, {
      authUser: { role: "ADMIN", keyName: "k" },
    });

    expect(await screen.findByText("user:U-1001")).toBeInTheDocument();
    expect(screen.getByText("merchant:M-2002")).toBeInTheDocument();
    expect(screen.getByText("Unusual user volume")).toBeInTheDocument();
    expect(screen.getByText("count: 3")).toBeInTheDocument();
    // 5 evidence ids -> 4 chips + "+1"
    expect(screen.getByText("TXN-D")).toBeInTheDocument();
    expect(screen.queryByText("TXN-E")).not.toBeInTheDocument();
    expect(screen.getByText("+1")).toBeInTheDocument();
    expect(
      screen.getByText(/Advisory only — signals never bypass the recovery policy or safety gate\./),
    ).toBeInTheDocument();
    expect(screen.getByText("graph-v1")).toBeInTheDocument();
  });

  it("caps entity chips at 8 with a '+n more' tag", async () => {
    mockGetRelationships.mockResolvedValue(
      makeRelationshipsResponse({
        entities: Array.from({ length: 10 }, (_, i) => ({
          type: "user",
          id: `U-${i}`,
        })),
      }),
    );

    renderWithProviders(<RelationshipsPanel transactionId="TXN-1" />, {
      authUser: { role: "ADMIN", keyName: "k" },
    });

    expect(await screen.findByText("user:U-0")).toBeInTheDocument();
    expect(screen.queryByText("user:U-8")).not.toBeInTheDocument();
    expect(screen.getByText("+2 more")).toBeInTheDocument();
  });

  it("renders a muted line on 403", async () => {
    mockGetRelationships.mockRejectedValue(
      apiError(403, "INSUFFICIENT_PERMISSIONS", "forbidden"),
    );

    renderWithProviders(<RelationshipsPanel transactionId="TXN-1" />, {
      authUser: { role: "ADMIN", keyName: "k" },
    });

    expect(
      await screen.findByText("Relationship signals are available to staff roles."),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// TransactionDetails integration
// ---------------------------------------------------------------------------

describe("TransactionDetails — Stage 11 panels", () => {
  it("renders the three Stage 11 panels alongside existing sections for staff", async () => {
    mockGetTransaction.mockResolvedValue(makeTransaction());
    mockGetTimeline.mockResolvedValue(makeTimeline(1));

    renderWithProviders(
      <Routes>
        <Route path="/transactions/:transactionId" element={<TransactionDetails />} />
      </Routes>,
      { route: "/transactions/TXN-1", authUser: { role: "ADMIN", keyName: "k" } }
    );

    expect(await screen.findByTestId("temporal-panel")).toBeInTheDocument();
    expect(screen.getByTestId("behavioral-signals-panel")).toBeInTheDocument();
    expect(screen.getByTestId("relationships-panel")).toBeInTheDocument();
    // Existing sections still present.
    expect(screen.getByRole("heading", { name: "Payment flow reconstruction" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Explanation/i })).toBeInTheDocument();
  });
});
