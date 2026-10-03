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
          logout/session-expiry redirects and would throw outside a Router.
          Future flags opt in to the v6→v7 behavior now (silences the console
          warnings and keeps the upgrade path smooth). */}
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
