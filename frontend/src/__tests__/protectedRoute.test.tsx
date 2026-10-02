import "../test/mocks";

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import type { ReactElement } from "react";
import { AuthProvider } from "../context/AuthContext";
import { ProtectedRoute } from "../router/ProtectedRoute";
import { mockGetMe } from "../test/mocks";
import { makeTestQueryClient } from "../test/utils";

afterEach(() => cleanup());

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

describe("ProtectedRoute", () => {
  it("redirects unauthenticated users to /login", async () => {
    // No stored session -> AuthProvider finishes loading with no user.
    renderWithRealAuth(<Harness />);

    expect(await screen.findByText("Login form")).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
  });

  it("renders protected content for an authenticated user", async () => {
    sessionStorage.setItem(
      "prdt.auth",
      JSON.stringify({ apiKey: "good-key", role: "ADMIN", keyName: "admin-key" })
    );
    mockGetMe.mockResolvedValue({ role: "ADMIN", key_name: "admin-key" });

    renderWithRealAuth(<Harness />);

    expect(await screen.findByText("Protected content")).toBeInTheDocument();
    expect(screen.queryByText("Login form")).not.toBeInTheDocument();
  });

  it("shows a loading state while the session is being validated", async () => {
    sessionStorage.setItem(
      "prdt.auth",
      JSON.stringify({ apiKey: "slow-key", role: "SUPPORT", keyName: "support-key" })
    );
    mockGetMe.mockImplementation(() => new Promise(() => {})); // never resolves

    renderWithRealAuth(<Harness />);

    await waitFor(() => {
      expect(screen.getByRole("status")).toBeInTheDocument();
    });
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
  });
});
