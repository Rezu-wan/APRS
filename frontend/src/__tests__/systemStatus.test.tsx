import "../test/mocks";

import { cleanup, screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import SystemStatus from "../pages/SystemStatus";
import type { DemoStatusResponse } from "../api/demo";
import { apiError, mockGetDemoStatus, resetApiMocks } from "../test/mocks";
import { renderWithProviders } from "../test/utils";

afterEach(() => cleanup());
beforeEach(() => {
  resetApiMocks();
  sessionStorage.clear();
});

function renderStatus(role: "SYSTEM" | "ADMIN" | "SUPPORT" = "SYSTEM") {
  return renderWithProviders(
    <Routes>
      <Route path="/status" element={<SystemStatus />} />
    </Routes>,
    { route: "/status", authUser: { role, keyName: "staff-key" } }
  );
}

function makeStatus(overrides: Partial<DemoStatusResponse> = {}): DemoStatusResponse {
  return {
    database: "connected",
    ml_models: "loaded",
    genai: { provider: "mock", status: "available", prompt_version: "v4" },
    sandbox_provider: {
      provider: "mock",
      initial_limit: 10000,
      available_limit: 8800,
      currency: "BDT",
      held_entries: 2,
      released_entries: 1,
    },
    ...overrides,
  };
}

describe("SystemStatus console", () => {
  it("renders all service rows from the default healthy status", async () => {
    mockGetDemoStatus.mockResolvedValue(makeStatus());
    renderStatus();

    expect(await screen.findByTestId("status-row-API")).toHaveTextContent("ONLINE");
    expect(screen.getByText("System status")).toBeInTheDocument();
    expect(screen.getByText("Simulated sandbox — no real money moves")).toBeInTheDocument();

    expect(screen.getByTestId("status-row-DATABASE")).toHaveTextContent("CONNECTED");
    expect(screen.getByTestId("status-row-ML MODELS")).toHaveTextContent("LOADED");
    expect(screen.getByTestId("status-row-GENAI")).toHaveTextContent("AVAILABLE");
    const sandbox = screen.getByTestId("status-row-SANDBOX PROVIDER");
    expect(sandbox).toHaveTextContent("READY");
    expect(sandbox).toHaveTextContent("8,800 BDT available · 2 held · 1 released");
    expect(screen.getByText(/GenAI provider: mock · prompt v4/)).toBeInTheDocument();
  });

  it("shows FALLBACK MODE with the deterministic note when GenAI is degraded", async () => {
    mockGetDemoStatus.mockResolvedValue(
      makeStatus({ genai: { provider: "openai", status: "fallback", prompt_version: "v4" } })
    );
    renderStatus();

    const genai = await screen.findByTestId("status-row-GENAI");
    expect(genai).toHaveTextContent("FALLBACK MODE");
    expect(genai).toHaveTextContent("deterministic fallback explanations — demo continues");
  });

  it("shows UNAVAILABLE GenAI and unavailable database honestly", async () => {
    mockGetDemoStatus.mockResolvedValue(
      makeStatus({
        database: "unavailable",
        genai: { provider: "none", status: "unavailable", prompt_version: "v4" },
      })
    );
    renderStatus();

    expect(await screen.findByTestId("status-row-DATABASE")).toHaveTextContent("UNAVAILABLE");
    expect(screen.getByTestId("status-row-GENAI")).toHaveTextContent("UNAVAILABLE");
    expect(screen.queryByText(/FALLBACK MODE/)).not.toBeInTheDocument();
  });

  it("renders an honest error state with retry when the status query fails", async () => {
    mockGetDemoStatus.mockRejectedValue(apiError(500, "UNKNOWN", "Status backend exploded"));
    renderStatus();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Status unavailable");
    expect(alert).toHaveTextContent("Status backend exploded");
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });
});
