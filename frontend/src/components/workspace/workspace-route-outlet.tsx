import { Suspense } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { ErrorBoundary } from "@/components/ui/error-boundary";
import { useI18n } from "@/core/i18n/hooks";

function WorkspaceRoutePending({ label }: { label: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy="true"
      className="flex h-full min-h-[220px] items-center justify-center px-4 text-sm text-muted-foreground"
    >
      {label}
    </div>
  );
}

/**
 * Keeps the workspace shell mounted while a cold route chunk loads, but resets
 * the Suspense boundary for each pathname. React Router navigations run in a
 * transition; without the pathname key React keeps showing the previous route
 * and the user gets no indication that navigation is in progress.
 */
export function WorkspaceRouteOutlet() {
  const { pathname } = useLocation();
  const { t } = useI18n();

  return (
    <Suspense
      key={pathname}
      fallback={<WorkspaceRoutePending label={t.common.loading} />}
    >
      <ErrorBoundary>
        <Outlet />
      </ErrorBoundary>
    </Suspense>
  );
}
