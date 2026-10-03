import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import {
  FlaskConical,
  Info,
  LayoutDashboard,
  LifeBuoy,
  LogOut,
  Menu,
  RefreshCcw,
  Scale,
  X,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";

const NAV_ITEMS = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard, staffOnly: false },
  { to: "/transactions", label: "Transactions", icon: RefreshCcw, staffOnly: false },
  { to: "/support", label: "Support", icon: LifeBuoy, staffOnly: true },
  { to: "/demo", label: "Demo", icon: FlaskConical, adminOnly: true },
  { to: "/simulator", label: "Simulator", icon: Scale, staffOnly: true },
];

const ROLE_CHIP_STYLES: Record<string, string> = {
  SYSTEM: "bg-indigo-50 text-indigo-700 ring-indigo-600/20",
  ADMIN: "bg-teal-50 text-teal-700 ring-teal-600/20",
  SUPPORT: "bg-amber-50 text-amber-700 ring-amber-600/20",
  CUSTOMER: "bg-slate-100 text-slate-600 ring-slate-500/20",
};

/** Slim always-visible sandbox disclaimer strip (staff AND customers). */
function SandboxBanner() {
  return (
    <div
      data-testid="sandbox-banner"
      role="note"
      className="border-b border-amber-200 bg-amber-50 px-4 py-1.5 text-center text-xs font-semibold tracking-wide text-amber-800 sm:px-6"
    >
      SIMULATED SANDBOX — NO REAL MONEY MOVES
    </div>
  );
}

export function AppLayout() {
  const { user, logout } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);

  if (!user) return null; // ProtectedRoute guarantees a user; narrow for TS.

  const isStaff = user.role !== "CUSTOMER";

  return (
    <LayoutShell
      isStaff={isStaff}
      role={user.role}
      mobileOpen={mobileOpen}
      setMobileOpen={setMobileOpen}
      logout={logout}
    />
  );
}

function LayoutShell({
  isStaff,
  role,
  mobileOpen,
  setMobileOpen,
  logout,
}: {
  isStaff: boolean;
  role: string;
  mobileOpen: boolean;
  setMobileOpen: (updater: (open: boolean) => boolean) => void;
  logout: () => void;
}) {
  const isAdmin = role === "SYSTEM" || role === "ADMIN";
  const navItems = NAV_ITEMS.filter((item) => {
    if (item.adminOnly) return isAdmin;
    if (item.staffOnly) return isStaff;
    return true;
  });

  return (
    <div className="min-h-screen bg-slate-50">
      <SandboxBanner />
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between gap-4 px-4 sm:px-6">
          <div className="flex items-center gap-3">
            <button
              type="button"
              className="rounded-md p-2 text-slate-500 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 md:hidden"
              aria-label={mobileOpen ? "Close navigation menu" : "Open navigation menu"}
              aria-expanded={mobileOpen}
              onClick={() => setMobileOpen((open) => !open)}
            >
              {mobileOpen ? (
                <X aria-hidden="true" className="h-5 w-5" />
              ) : (
                <Menu aria-hidden="true" className="h-5 w-5" />
              )}
            </button>
            <span className="text-sm font-semibold tracking-tight text-slate-900 sm:text-base">
              Payment Recovery Digital Twin
            </span>
          </div>

          <nav aria-label="Main navigation" className="hidden md:block">
            <ul className="flex items-center gap-1">
              {navItems.map(({ to, label, icon: Icon }) => (
                <li key={to}>
                  <NavLink
                    to={to}
                    className={({ isActive }) =>
                      `flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
                        isActive
                          ? "bg-indigo-50 text-indigo-700"
                          : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                      }`
                    }
                  >
                    <Icon aria-hidden="true" className="h-4 w-4" />
                    {label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>

          <div className="flex items-center gap-3">
            <span
              className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${
                ROLE_CHIP_STYLES[role] ?? ROLE_CHIP_STYLES.CUSTOMER
              }`}
            >
              {role}
            </span>
            <button
              type="button"
              onClick={logout}
              className="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
            >
              <LogOut aria-hidden="true" className="h-4 w-4" />
              <span className="hidden sm:inline">Log out</span>
              <span className="sr-only sm:hidden">Log out</span>
            </button>
          </div>
        </div>

        {mobileOpen && (
          <nav aria-label="Mobile navigation" className="border-t border-slate-200 md:hidden">
            <ul className="space-y-1 px-4 py-3">
              {navItems.map(({ to, label, icon: Icon }) => (
                <li key={to}>
                  <NavLink
                    to={to}
                    onClick={() => setMobileOpen((open) => !open)}
                    className={({ isActive }) =>
                      `flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
                        isActive
                          ? "bg-indigo-50 text-indigo-700"
                          : "text-slate-600 hover:bg-slate-100"
                      }`
                    }
                  >
                    <Icon aria-hidden="true" className="h-4 w-4" />
                    {label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
        )}
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        {role === "CUSTOMER" && <CustomerBanner />}
        <Outlet />
      </main>
    </div>
  );
}

function CustomerBanner() {
  const [dismissed, setDismissed] = useState(false);
  if (dismissed) return null;
  return (
    <div
      role="note"
      className="mb-6 flex items-start gap-3 rounded-lg border border-indigo-100 bg-indigo-50 p-4 text-sm text-indigo-900"
    >
      <Info aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
      <p className="flex-1">
        You are signed in as a customer — you see only your own transactions. Use the dashboard
        for an overview or open Transactions to browse, filter, and report problems.
      </p>
      <button
        type="button"
        onClick={() => setDismissed(true)}
        aria-label="Dismiss notice"
        className="rounded-md p-1 text-indigo-500 hover:bg-indigo-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-600"
      >
        <X aria-hidden="true" className="h-4 w-4" />
      </button>
    </div>
  );
}
