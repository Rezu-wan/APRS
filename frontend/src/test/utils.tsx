// Test render helper: MemoryRouter + fresh QueryClient + optional auth stub.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ComponentProps, ReactElement, ReactNode } from "react";
import { AuthContext } from "../context/AuthContext";
import type { Role } from "../types/api";

export interface AuthUserStub {
  role: Role;
  keyName: string;
  customerId?: string | null;
}

interface RenderWithProvidersOptions extends Omit<RenderOptions, "wrapper"> {
  route?: string;
  authUser?: AuthUserStub | null;
}

/** A fresh QueryClient tuned for tests (no retries, no caching). */
export function makeTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false, gcTime: 0 },
    },
  });
}

/**
 * Renders `ui` inside MemoryRouter (at `route`) and a per-test QueryClient.
 * When `authUser` is provided, the real AuthProvider is bypassed with a stub
 * context value (no backend session validation). When omitted, the context
 * value is left as-is so real providers used by the component itself apply.
 */
export function renderWithProviders(
  ui: ReactElement,
  { route = "/", authUser, ...options }: RenderWithProvidersOptions = {}
) {
  const queryClient = makeTestQueryClient();

  const authValue = authUser
    ? ({
        user: authUser,
        isLoading: false,
        login: vi.fn<(apiKey: string) => Promise<{ role: Role; key_name: string }>>()
          .mockResolvedValue({ role: authUser.role, key_name: authUser.keyName }),
        logout: vi.fn(),
      } as unknown as ComponentProps<typeof AuthContext.Provider>["value"])
    : undefined;

  function Wrapper({ children }: { children: ReactNode }) {
    const routed = <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>;
    if (authValue) {
      return (
        <QueryClientProvider client={queryClient}>
          <AuthContext.Provider value={authValue}>{routed}</AuthContext.Provider>
        </QueryClientProvider>
      );
    }
    // No authUser: leave auth context untouched so tests can supply a real
    // AuthProvider themselves (or components that do not need it run bare).
    return routed;
  }

  return { ...render(ui, { wrapper: Wrapper, ...options }), queryClient };
}
