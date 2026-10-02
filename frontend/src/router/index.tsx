import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppLayout } from "../layouts/AppLayout";
import { ProtectedRoute } from "./ProtectedRoute";
import { LoadingState } from "../components/ui/LoadingState";
import { Login } from "../pages/Login";
import { NotFound } from "../pages/NotFound";

const Dashboard = lazy(() => import("../pages/Dashboard"));
const TransactionSearch = lazy(() => import("../pages/TransactionSearch"));
const TransactionDetails = lazy(() => import("../pages/TransactionDetails"));

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
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
