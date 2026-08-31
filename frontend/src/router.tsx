import { lazy, Suspense, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { ProtectedRoute } from "@/components/auth/protected-route";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import {
  ElectronTitleBar,
  ElectronTitleBarProvider,
} from "@/components/electron-title-bar";
import { useI18n } from "@/core/i18n/hooks";
import { createWorkspaceRoute } from "@/app/workspace/workspace-routes";

const AboutPage = lazy(() => import("./app/about/page"));
const TermsPage = lazy(() => import("./app/terms/page"));
const PrivacyPage = lazy(() => import("./app/privacy/page"));
const PublicThreadSharePage = lazy(() => import("./app/share/[token]/page"));
const DesktopPage = lazy(() => import("./app/desktop/page"));
const TopBrowserPage = lazy(() => import("./app/browser/page"));
const MediaAppPage = lazy(() => import("./app/apps/media/page"));
const SLOW_PAGE_LOADING_MS = 8_000;

function LegacyAccountRouteRedirect() {
  const location = useLocation();
  return <Navigate to={`/desktop${location.search}`} replace />;
}

export function PageLoading() {
  const { t } = useI18n();
  const [isSlow, setIsSlow] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(
      () => setIsSlow(true),
      SLOW_PAGE_LOADING_MS,
    );
    return () => window.clearTimeout(timer);
  }, []);

  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy="true"
      className="flex h-full min-h-[320px] w-full items-start justify-center p-4 sm:p-6"
    >
      {!isSlow ? <span className="sr-only">{t.common.loading}</span> : null}
      <div
        className="w-full max-w-6xl animate-pulse space-y-4"
        data-testid="page-loading-skeleton"
      >
        <div className="flex items-center justify-between gap-4">
          <div className="space-y-2">
            <div className="h-5 w-32 rounded-md bg-muted" />
            <div className="h-3 w-56 max-w-[60vw] rounded bg-muted/70" />
          </div>
          <div className="h-8 w-24 rounded-lg bg-muted" />
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, index) => (
            <div
              key={index}
              className="h-28 rounded-xl border border-border-subtle bg-muted/35"
            />
          ))}
        </div>
        {isSlow ? (
          <div className="flex animate-none flex-col items-center gap-2 pt-2 text-center">
            <div className="text-sm text-muted-foreground">
              {t.common.loadingWorkspace}
            </div>
            <button
              type="button"
              className="rounded-md border border-border-default px-3 py-1.5 text-xs text-foreground transition hover:bg-muted"
              onClick={() => window.location.reload()}
            >
              {t.conversation.retry}
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function AppRouter() {
  return (
    <ErrorBoundary>
      <ElectronTitleBarProvider>
        <ElectronTitleBar />
        <Suspense fallback={<PageLoading />}>
          <Routes>
            <Route path="/" element={<Navigate to="/desktop" replace />} />
            <Route path="/login" element={<LegacyAccountRouteRedirect />} />
            <Route path="/register" element={<LegacyAccountRouteRedirect />} />
            <Route path="/about" element={<AboutPage />} />
            <Route path="/terms" element={<TermsPage />} />
            <Route path="/privacy" element={<PrivacyPage />} />
            {/* Capability-token snapshots are intentionally outside the auth
              guard and workspace shell. The public endpoint already returns
              a bounded, sanitised, read-only projection. */}
            <Route path="/share/:token" element={<PublicThreadSharePage />} />
            {/* The desktop owns the only login boundary. Workbench routes
              inherit its HttpOnly session and never render a second login. */}
            <Route path="/desktop" element={<DesktopPage />} />

            <Route element={<ProtectedRoute />}>
              <Route
                path="/settings"
                element={<Navigate to="/workspace/settings" replace />}
              />
              <Route path="/browser" element={<TopBrowserPage />} />
              <Route
                path="/apps/photos"
                element={<MediaAppPage kind="image" />}
              />
              <Route
                path="/apps/media"
                element={<MediaAppPage kind="video" />}
              />
              <Route
                path="/plugins"
                element={
                  <Navigate
                    to="/workspace/agents?surface=chat&tab=plugins"
                    replace
                  />
                }
              />

              {createWorkspaceRoute()}
            </Route>

            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </ElectronTitleBarProvider>
    </ErrorBoundary>
  );
}
