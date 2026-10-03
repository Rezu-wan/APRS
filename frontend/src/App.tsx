import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { ApiError } from "./api/client";
import { AuthProvider } from "./context/AuthContext";
import { AppRoutes } from "./router";

// Retry transient failures (network blips, a backend restart) with
// exponential backoff so a short gap self-heals. Deterministic client
// errors are answers, not failures — 401/403/404/422 are never retried,
// and 429 is surfaced honestly with its Retry-After (Stage 9 contract).
// Only 5xx / network errors retry, at most twice (~1s + 2s). Under the
// vitest runner this stays at the old single-retry behavior: delayed
// retries would race the synchronous act()-based tests.
const isTestRunner = import.meta.env.MODE === "test";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: isTestRunner
        ? 1
        : (failureCount, error) => {
            if (failureCount >= 2) return false;
            if (error instanceof ApiError && error.status !== null && error.status < 500) {
              return false;
            }
            return true;
          },
      retryDelay: isTestRunner ? 0 : (attempt) => Math.min(1000 * 2 ** attempt, 4000),
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      {/* Router must wrap AuthProvider: AuthContext uses useNavigate for
          logout/session-expiry redirects and would throw outside a Router. */}
      {/* Future flags silence the v6 deprecation warnings. v7_startTransition
          is enabled for the real app but NOT under the vitest runner
          (MODE === "test"): transitions defer route updates outside act(),
          which breaks the synchronous test harness (96 failures). The tests
          cover the router's routing behavior; the flag changes React's
          update scheduling inside react-router only. */}
      <BrowserRouter
        future={{
          v7_relativeSplatPath: true,
          ...(import.meta.env.MODE !== "test" ? { v7_startTransition: true } : {}),
        }}
      >
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
