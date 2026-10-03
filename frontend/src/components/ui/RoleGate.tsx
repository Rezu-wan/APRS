import type { ReactNode } from "react";
import { ShieldAlert } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import type { Role } from "../../types/api";

function AccessDenied() {
  return (
    <div
      role="alert"
      className="mx-auto mt-12 flex max-w-md flex-col items-center gap-3 rounded-lg border border-slate-200 bg-white p-8 text-center shadow-sm"
    >
      <ShieldAlert aria-hidden="true" className="h-10 w-10 text-amber-500" />
      <h2 className="text-lg font-semibold text-slate-900">Access denied</h2>
      <p className="text-sm text-slate-500">
        Your role does not have permission to view this content.
      </p>
    </div>
  );
}

interface RoleGateProps {
  allowed: Role[];
  children: ReactNode;
  /** Render nothing instead of the AccessDenied card (for sections that are
   * simply not part of a narrower role's page, e.g. staff panels on the
   * customer transaction view). */
  silent?: boolean;
}

/** Renders children only when the current user's role is in `allowed`; otherwise
 * AccessDenied (or nothing when `silent`). */
export function RoleGate({ allowed, children, silent = false }: RoleGateProps) {
  const { user } = useAuth();
  if (!user || !allowed.includes(user.role)) {
    return silent ? null : <AccessDenied />;
  }
  return <>{children}</>;
}
