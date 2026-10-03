import "../test/mocks";

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import type { ReactElement } from "react";
import { AuthProvider } from "../context/AuthContext";
import { Login } from "../pages/Login";
import { ProtectedRoute } from "../router/ProtectedRoute";
import { clearSession, setSession } from "../lib/session";
import { mockGetMe, mockGetMeWithKey } from "../test/mocks";
import { makeTestQueryClient } from "../test/utils";

afterEach(() => cleanup());
beforeEach(() => {
  // the api key lives in module memory next to sessionStorage — clear both
  // so primed sessions never leak between tests
  sessionStorage.clear();
  clearSession();
});

function renderWithRealAuth(ui: ReactElement, route = "/dashboard") {
  // AuthProvider calls useNavigate, so it must render inside the Router.
  return render(
    <MemoryRouter initialEntries={[route]}>
      <QueryClientProvider client={makeTestQueryClient()}>
        <AuthProvider>{ui}</AuthProvider>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

function Harness() {
  return (
    <Routes>
      <Route path="/login" element={<div>Login form</div>} />
      <Route
        path="/dashboard"
        element={
          <ProtectedRoute>
            <div>Protected content</div>
          </ProtectedRoute>
        }
      />
    </Routes>
  );
}

function TransactionsHarness() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/transactions"
        element={
          <ProtectedRoute>
            <div>Transactions list</div>
          </ProtectedRoute>
        }
      />
    </Routes>
  );
}

describe("ProtectedRoute", () => {
  it("redirects unauthenticated users to /login", async () => {
    // No stored session -> AuthProvider finishes loading with no user.
    renderWithRealAuth(<Harness />);

    expect(await screen.findByText("Login form")).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
  });

  it("renders protected content for an authenticated user", async () => {
    // prime the way Login does: key in module memory, record in sessionStorage
    setSession({ apiKey: "good-key", role: "ADMIN", keyName: "admin-key" });
    mockGetMe.mockResolvedValue({ role: "ADMIN", key_name: "admin-key" });

    renderWithRealAuth(<Harness />);

    expect(await screen.findByText("Protected content")).toBeInTheDocument();
    expect(screen.queryByText("Login form")).not.toBeInTheDocument();
  });

  it("shows a loading state while the session is being validated", async () => {
    setSession({ apiKey: "slow-key", role: "SUPPORT", keyName: "support-key" });
    mockGetMe.mockImplementation(() => new Promise(() => {})); // never resolves

    renderWithRealAuth(<Harness />);

    await waitFor(() => {
      expect(screen.getByRole("status")).toBeInTheDocument();
    });
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
  });

  it("returns to the bounced path after signing in on the login page", async () => {
    // Reload-on-a-protected-path flow: bounce to /login with state.from,
    // sign in there, and land back on /transactions (not the dashboard).
    mockGetMeWithKey.mockResolvedValue({ role: "ADMIN", key_name: "admin-key" });

    renderWithRealAuth(<TransactionsHarness />, "/transactions");
    expect(await screen.findByLabelText("API key")).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("API key"), "good-key");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText("Transactions list")).toBeInTheDocument();
    expect(screen.queryByLabelText("API key")).not.toBeInTheDocument();
  });
});
