import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext";
import { AppRoutes } from "./router";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
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
      {/* v7_relativeSplatPath is a safe opt-in; v7_startTransition is left
          OFF deliberately — it defers route updates into transitions, which
          breaks the synchronous act()-based test harness (96 tests). The
          remaining dev-only warning is harmless. */}
      <BrowserRouter future={{ v7_relativeSplatPath: true }}>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
