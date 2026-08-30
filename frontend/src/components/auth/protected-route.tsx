import { Navigate, Outlet, useLocation } from "react-router-dom";
import { LoadingState } from "@/components/ui/state";
import { loginPathWithReturnTo } from "@/core/auth/return-to";
import { useI18n } from "@/core/i18n/hooks";
import { useAuth } from "@/providers/AuthProvider";

/**
 * Route guard that redirects unauthenticated users to /login.
 *
 * When auth is disabled on the backend (authStatus.enabled === false),
 * all users are allowed through — matching the backend's behavior.
 */
export function ProtectedRoute() {
  const location = useLocation();
  const { isLoading, authStatus, isAuthenticated } = useAuth();
  const { t } = useI18n();

  // Still loading auth status — show nothing to avoid flash
  if (isLoading) {
    return (
      <LoadingState className="h-screen" title={t.common.loadingWorkspace} />
    );
  }

  // Auth disabled on backend — let everyone through
  if (authStatus && !authStatus.enabled) {
    return <Outlet />;
  }

  // Auth enabled requires a real authenticated account.
  if (!isAuthenticated) {
    const returnTo = `${location.pathname}${location.search}${location.hash}`;
    return <Navigate to={loginPathWithReturnTo(returnTo)} replace />;
  }

  return <Outlet />;
}
