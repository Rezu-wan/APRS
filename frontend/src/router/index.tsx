import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppLayout } from "../layouts/AppLayout";
import { ProtectedRoute } from "./ProtectedRoute";
import { LoadingState } from "../components/ui/LoadingState";
import { RoleGate } from "../components/ui/RoleGate";
import { Login } from "../pages/Login";
import { NotFound } from "../pages/NotFound";

const Dashboard = lazy(() => import("../pages/Dashboard"));
const TransactionSearch = lazy(() => import("../pages/TransactionSearch"));
const TransactionDetails = lazy(() => import("../pages/TransactionDetails"));
const DemoMode = lazy(() => import("../pages/DemoMode"));
const SystemStatus = lazy(() => import("../pages/SystemStatus"));
const PolicySimulator = lazy(() => import("../pages/PolicySimulator"));
const ChaosLab = lazy(() => import("../pages/ChaosLab"));
const SupportOverview = lazy(() => import("../pages/support/SupportOverview"));
const CustomerProfile = lazy(() => import("../pages/support/CustomerProfile"));
const SupportTransaction = lazy(() => import("../pages/support/SupportTransaction"));

/** Staff-only wrapper for the support workspace — the backend enforces the
 * same role list per request; this only avoids rendering the shell. */
function SupportOnly({ children }: { children: ReactNode }) {
  return (
    <RoleGate allowed={["SYSTEM", "ADMIN", "SUPPORT"]}>
      <LazyPage>{children}</LazyPage>
    </RoleGate>
  );
}

function LazyPage({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<LoadingState />}>{children}</Suspense>
  );
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <ProtectedRoute>
            <AppLayout />
          </ProtectedRoute>
        }
      >
        <Route
          path="/dashboard"
          element={
            <LazyPage>
              <Dashboard />
            </LazyPage>
          }
        />
        <Route
          path="/transactions"
          element={
            <LazyPage>
              <TransactionSearch />
            </LazyPage>
          }
        />
        <Route
          path="/transactions/:transactionId"
          element={
            <LazyPage>
              <TransactionDetails />
            </LazyPage>
          }
        />
        <Route
          path="/support"
          element={
            <SupportOnly>
              <SupportOverview />
            </SupportOnly>
          }
        />
        <Route
          path="/support/customers/:customerId"
          element={
            <SupportOnly>
              <CustomerProfile />
            </SupportOnly>
          }
        />
        <Route
          path="/support/transactions/:transactionId"
          element={
            <SupportOnly>
              <SupportTransaction />
            </SupportOnly>
          }
        />
        <Route
          path="/demo"
          element={
            <LazyPage>
              <DemoMode />
            </LazyPage>
          }
        />
        <Route
          path="/simulator"
          element={
            <LazyPage>
              <PolicySimulator />
            </LazyPage>
          }
        />
        <Route
          path="/chaos"
          element={
            <LazyPage>
              <ChaosLab />
            </LazyPage>
          }
        />
        <Route
          path="/status"
          element={
            <LazyPage>
              <SystemStatus />
            </LazyPage>
          }
        />
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
