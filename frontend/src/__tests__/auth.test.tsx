// NOTE: mocks import MUST come before any module that transitively imports
// the api layer, so the mock registry is populated first.
import "../test/mocks";

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AuthProvider, useAuth } from "../context/AuthContext";
import { Login } from "../pages/Login";
import { apiError, mockGetMe, mockGetMeWithKey, resetApiMocks } from "../test/mocks";
import { makeTestQueryClient } from "../test/utils";

afterEach(() => cleanup());
beforeEach(() => {
  resetApiMocks();
  sessionStorage.clear();
});

function DashboardStub() {
  const { user, logout } = useAuth();
  return (
    <div>
      <span>Dashboard loaded</span>
      <span>role: {user?.role}</span>
      <button type="button" onClick={logout}>
        Log out
      </button>
    </div>
  );
}

function Harness() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/dashboard" element={<DashboardStub />} />
    </Routes>
  );
}

function renderLogin() {
  // AuthProvider calls useNavigate, so it must render inside the Router.
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <QueryClientProvider client={makeTestQueryClient()}>
        <AuthProvider>
          <Harness />
        </AuthProvider>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

describe("Login", () => {
  it("shows an error when the API key is invalid (401)", async () => {
    mockGetMeWithKey.mockRejectedValue(apiError(401, "UNAUTHORIZED", "Invalid API key"));
    renderLogin();

    await userEvent.type(screen.getByLabelText("API key"), "bad-key");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Invalid API key");
    // Still on the login form.
    expect(screen.getByLabelText("API key")).toBeInTheDocument();
  });

  it("stores the session and navigates to /dashboard on success", async () => {
    mockGetMeWithKey.mockResolvedValue({ role: "ADMIN", key_name: "admin-key" });
    renderLogin();

    await userEvent.type(screen.getByLabelText("API key"), "good-key");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText("Dashboard loaded")).toBeInTheDocument();

    const raw = sessionStorage.getItem("prdt.auth");
    expect(raw).not.toBeNull();
    const session = JSON.parse(raw as string) as {
      apiKey: string;
      role: string;
      keyName: string;
    };
    expect(session).toMatchObject({ apiKey: "good-key", role: "ADMIN", keyName: "admin-key" });
  });

  it("logout clears the session and returns to /login", async () => {
    // Prime a stored session; AuthProvider validates it on mount via getMe.
    sessionStorage.setItem(
      "prdt.auth",
      JSON.stringify({ apiKey: "good-key", role: "ADMIN", keyName: "admin-key" })
    );
    mockGetMe.mockResolvedValue({ role: "ADMIN", key_name: "admin-key" });

    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <QueryClientProvider client={makeTestQueryClient()}>
          <AuthProvider>
            <Harness />
          </AuthProvider>
        </QueryClientProvider>
      </MemoryRouter>
    );

    // Wait for session validation to complete and dashboard to show.
    expect(await screen.findByText("Dashboard loaded")).toBeInTheDocument();
    expect(sessionStorage.getItem("prdt.auth")).not.toBeNull();

    await userEvent.click(screen.getByRole("button", { name: /log out/i }));

    await waitFor(() => {
      expect(sessionStorage.getItem("prdt.auth")).toBeNull();
    });
    expect(await screen.findByLabelText("API key")).toBeInTheDocument();
  });
});
