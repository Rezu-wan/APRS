import "../test/mocks";

import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import DemoMode from "../pages/DemoMode";
import type { DemoScenarioStatus } from "../api/demo";
import { mockGetDemoScenarios, mockResetDemo, resetApiMocks } from "../test/mocks";
import { renderWithProviders } from "../test/utils";

afterEach(() => cleanup());
beforeEach(() => {
  resetApiMocks();
  sessionStorage.clear();
});

function makeScenario(
  key: DemoScenarioStatus["key"],
  overrides: Partial<DemoScenarioStatus> = {}
): DemoScenarioStatus {
  return {
    key,
    transaction_id: `DEMO-${key}`,
    title: `${key} — demo scenario`,
    subtitle: `Subtitle for ${key}`,
    story: `Story for ${key}.`,
    expected: {
      anomaly: "GENUINE_FAILURE",
      risk_level: "LOW",
      decision: "AUTO_RECOVERED",
      status: "VERIFIED",
    },
    exists: true,
    current_state: "RECOVERY_PENDING",
    prepared: true,
    processed: false,
    recovery: null,
    risk: { anomaly_type: "GENUINE_FAILURE", risk_level: "LOW" },
    ...overrides,
  };
}

const KEYS: DemoScenarioStatus["key"][] = ["S1", "S2", "S3", "S4", "S5", "S6"];

function mockSixScenarios() {
  const scenarios = KEYS.map((key) => makeScenario(key));
  mockGetDemoScenarios.mockResolvedValue({
    simulated: true,
    scenarios,
  });
  return scenarios;
}

function renderDemo(authUser: { role: "SYSTEM" | "ADMIN" | "SUPPORT" | "CUSTOMER"; keyName: string }) {
  return renderWithProviders(
    <Routes>
      <Route path="/demo" element={<DemoMode />} />
    </Routes>,
    { route: "/demo", authUser }
  );
}

describe("DemoMode (Demo Control Panel)", () => {
  it("renders six scenario cards with live status for staff", async () => {
    mockSixScenarios();
    renderWithProviders(
      <Routes>
        <Route path="/demo" element={<DemoMode />} />
      </Routes>,
      { route: "/demo", authUser: { role: "SYSTEM", keyName: "system-key" } }
    );

    expect(await screen.findByText(`${KEYS[0]} — demo scenario`)).toBeInTheDocument();
    expect(screen.getByText("Hackathon demo mode")).toBeInTheDocument();
    expect(screen.getByText("Simulated sandbox — no real money moves")).toBeInTheDocument();
    for (const key of KEYS) {
      expect(screen.getByText(`${key} — demo scenario`)).toBeInTheDocument();
      // Live status line (exists/prepared + current_state) — one per scenario.
      expect(screen.getAllByText(/state: RECOVERY_PENDING/)).toHaveLength(6);
      const links = screen.getAllByRole("link", { name: "Open transaction →" });
      expect(links).toHaveLength(6);
      expect(
        links.some((l) => l.getAttribute("href") === `/transactions/DEMO-${key}`)
      ).toBe(true);
    }
    expect(screen.getAllByRole("button", { name: "Prepare" })).toHaveLength(6);
  });

  it("shows the RoleGate blocked line for CUSTOMER and no action buttons", async () => {
    mockSixScenarios();
    renderDemo({ role: "CUSTOMER", keyName: "cust-key" });

    expect(await screen.findByText("Access denied")).toBeInTheDocument();
    expect(
      screen.getByText(/Your role does not have permission to view this content\./)
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Prepare" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /reset demo/i })).not.toBeInTheDocument();
  });

  it("denies access for SUPPORT role (demo is admin-only)", async () => {
    mockSixScenarios();
    renderWithProviders(
      <Routes>
        <Route path="/demo" element={<DemoMode />} />
      </Routes>,
      { route: "/demo", authUser: { role: "SUPPORT", keyName: "support-key" } }
    );

    // SUPPORT sees access denied — demo is now admin-only
    expect(await screen.findByText("Access denied")).toBeInTheDocument();
    expect(screen.getByText("Your role does not have permission to view this content.")).toBeInTheDocument();
    expect(screen.queryByText("Hackathon demo mode")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Prepare" })).not.toBeInTheDocument();
  });

  it("requires confirmation for reset: Cancel does not call the API", async () => {
    mockSixScenarios();
    renderWithProviders(
      <Routes>
        <Route path="/demo" element={<DemoMode />} />
      </Routes>,
      { route: "/demo", authUser: { role: "ADMIN", keyName: "admin-key" } }
    );

    await userEvent.click(await screen.findByRole("button", { name: /reset demo/i }));

    expect(
      screen.getByText(/Reset sandbox demo state\? This affects simulated data only\./)
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(mockResetDemo).not.toHaveBeenCalled();
    expect(
      screen.queryByText(/Reset sandbox demo state\? This affects simulated data only\./)
    ).not.toBeInTheDocument();
  });

  it("calls resetDemo after Confirm and shows the success note", async () => {
    const scenarios = mockSixScenarios();
    mockResetDemo.mockResolvedValue({
      reset: true,
      simulated: true,
      scenarios,
    });
    renderWithProviders(
      <Routes>
        <Route path="/demo" element={<DemoMode />} />
      </Routes>,
      { route: "/demo", authUser: { role: "ADMIN", keyName: "admin-key" } }
    );

    await userEvent.click(await screen.findByRole("button", { name: /reset demo/i }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => {
      expect(mockResetDemo).toHaveBeenCalledTimes(1);
    });
    expect(await screen.findByTestId("reset-success")).toHaveTextContent(/reset/i);
  });

  it("shows the inject button only on the S5 card", async () => {
    mockSixScenarios();
    renderWithProviders(
      <Routes>
        <Route path="/demo" element={<DemoMode />} />
      </Routes>,
      { route: "/demo", authUser: { role: "SYSTEM", keyName: "system-key" } }
    );

    expect(await screen.findByTestId("inject-S5")).toBeInTheDocument();
    expect(screen.getAllByTestId(/^inject-S/)).toHaveLength(1);
  });

  it("renders an honest empty state when the backend returns no scenarios", async () => {
    renderWithProviders(
      <Routes>
        <Route path="/demo" element={<DemoMode />} />
      </Routes>,
      { route: "/demo", authUser: { role: "SYSTEM", keyName: "system-key" } }
    );

    expect(await screen.findByText("No demo scenarios configured")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Prepare" })).not.toBeInTheDocument();
  });
});
