import { DetachedRouterContext } from "./detached-router-context";
import { Suspense, useCallback, useContext, useEffect, useRef } from "react";
import { WorkspaceArtifactRequestContext } from "./workspace-artifact-request";
import {
  MemoryRouter,
  Navigate,
  Route,
  Routes,
  useNavigate,
  useLocation,
  parsePath,
} from "react-router-dom";

import { createWorkspaceRoute } from "@/app/workspace/workspace-routes";
import { EchoDesktopWindowChromeContext } from "@/components/workspace/embedded-window-bridge";
import { normalizeWorkspaceRoute } from "@/core/router/desktop-workspace-route";

function EmbeddedWorkspaceLoading() {
  return (
    <div
      role="status"
      aria-live="polite"
      className="grid size-full min-h-0 place-items-center bg-background text-sm text-muted-foreground"
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

/**
 * Render the real Agent workspace inside an Echo OS window.
 *
 * This is intentionally only a routing boundary. Conversation rendering,
 * streaming, files, research, projects, agents, observability and every other
 * workspace surface come from the canonical components used by AppRouter.
 */
function WorkspaceLocationReporter({
  onRouteChange,
}: {
  onRouteChange?: (route: string) => void;
}) {
  const { pathname, search, hash } = useLocation();
  useEffect(() => {
    onRouteChange?.(`${pathname}${search}${hash}`);
  }, [pathname, search, hash, onRouteChange]);
  return null;
}

function WorkspaceArtifactRequestReceiver() {
  const request = useContext(WorkspaceArtifactRequestContext);
  const consumed = useRef<typeof request>(undefined);
  const location = useLocation();
  const navigate = useNavigate();
  useEffect(() => {
    if (!request || consumed.current === request) return;
    consumed.current = request;
    const params = new URLSearchParams(location.search);
    params.set("artifact", request.path);
    params.set("artifactRequest", String(request.revision));
    navigate(
      {
        pathname: location.pathname,
        search: `?${params}`,
        hash: location.hash,
      },
      { replace: true, state: location.state },
    );
  }, [request, location, navigate]);
  return null;
}

export function EmbeddedAgentWorkspace({
  initialRoute = "/workspace/realtime/new",
  initialState,
  onRouteChange,
}: {
  initialRoute?: string;
  initialState?: unknown;
  onRouteChange?: (route: string) => void;
}) {
  const outerNavigate = useNavigate();
  const openDesktopBrowser = useCallback(
    () => outerNavigate("/browser"),
    [outerNavigate],
  );
  const entry =
    normalizeWorkspaceRoute(initialRoute) ?? "/workspace/realtime/new";

  return (
    <div
      data-testid="embedded-agent-workspace"
      data-workspace-surface="canonical"
      className="size-full min-h-0 overflow-hidden bg-background text-foreground"
    >
      <EchoDesktopWindowChromeContext.Provider value>
        <DetachedRouterContext>
          <MemoryRouter
            key={entry}
            initialEntries={[{ ...parsePath(entry), state: initialState }]}
          >
            <WorkspaceLocationReporter onRouteChange={onRouteChange} />
            <WorkspaceArtifactRequestReceiver />
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
