import { Suspense, useCallback, useEffect, type ReactNode } from "react";
import {
  MemoryRouter,
  Navigate,
  Route,
  Routes,
  UNSAFE_LocationContext,
  UNSAFE_RouteContext,
  useNavigate,
} from "react-router-dom";

import { createWorkspaceRoute } from "@/app/workspace/workspace-routes";
import { EchoDesktopWindowChromeContext } from "@/components/workspace/embedded-window-bridge";

const DETACHED_ROUTE_CONTEXT = {
  outlet: null,
  matches: [],
  isDataRoute: false,
};

function DetachedRouterContext({ children }: { children: ReactNode }) {
  // Echo itself already runs in a HashRouter. The Agent window needs its own
  // history so links, thread changes and redirects stay inside that window.
  // Reset only the inherited router contexts, then mount a normal MemoryRouter
  // backed by the exact same route tree as the top-level Agent workspace.
  return (
    <UNSAFE_LocationContext.Provider value={null!}>
      <UNSAFE_RouteContext.Provider value={DETACHED_ROUTE_CONTEXT}>
        {children}
      </UNSAFE_RouteContext.Provider>
    </UNSAFE_LocationContext.Provider>
  );
}

function EmbeddedWorkspaceLoading() {
  return (
    <div
      role="status"
      aria-live="polite"
      className="grid size-full min-h-[360px] place-items-center bg-background text-sm text-muted-foreground"
    >
      正在加载完整 Agent 工作台…
    </div>
  );
}

function OpenDesktopBrowser({ onOpen }: { onOpen: () => void }) {
  useEffect(() => {
    onOpen();
  }, [onOpen]);
  return <EmbeddedWorkspaceLoading />;
}

function normalizedWorkspaceRoute(route: string): string {
  const trimmed = route.trim();
  return trimmed.startsWith("/workspace") ? trimmed : "/workspace/realtime/new";
}

/**
 * Render the real Agent workspace inside an Echo OS window.
 *
 * This is intentionally only a routing boundary. Conversation rendering,
 * streaming, files, research, projects, agents, observability and every other
 * workspace surface come from the canonical components used by AppRouter.
 */
export function EmbeddedAgentWorkspace({
  initialRoute = "/workspace/realtime/new",
}: {
  initialRoute?: string;
}) {
  const outerNavigate = useNavigate();
  const openDesktopBrowser = useCallback(
    () => outerNavigate("/browser"),
    [outerNavigate],
  );
  const entry = normalizedWorkspaceRoute(initialRoute);

  return (
    <div
      data-testid="embedded-agent-workspace"
      data-workspace-surface="canonical"
      className="size-full min-h-[360px] overflow-hidden bg-background text-foreground"
    >
      <EchoDesktopWindowChromeContext.Provider value>
        <DetachedRouterContext>
          <MemoryRouter key={entry} initialEntries={[entry]}>
            <Suspense fallback={<EmbeddedWorkspaceLoading />}>
              <Routes>
                {createWorkspaceRoute({
                  embeddedInWindow: true,
                  onOpenBrowser: openDesktopBrowser,
                })}
                <Route
                  path="/browser"
                  element={<OpenDesktopBrowser onOpen={openDesktopBrowser} />}
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
                <Route
                  path="/settings"
                  element={<Navigate to="/workspace/settings" replace />}
                />
                <Route
                  path="*"
                  element={<Navigate to="/workspace/realtime/new" replace />}
                />
              </Routes>
            </Suspense>
          </MemoryRouter>
        </DetachedRouterContext>
      </EchoDesktopWindowChromeContext.Provider>
    </div>
  );
}

export default EmbeddedAgentWorkspace;
