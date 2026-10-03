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
