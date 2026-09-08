import { useCallback } from "react";
import { normalizeWorkspaceRoute } from "@/core/router/desktop-workspace-route";
import { threadIdForWorkspaceRoute } from "@/core/router/task-workspace-route";
import { uuid } from "@/core/utils/uuid";
import { resolveAgentAppUrl } from "./agent-workspace";
import { EmbeddedAgentWorkspace } from "./embedded-agent-workspace";
import { useDesktopWindows } from "./use-desktop-windows";
import { WorkspaceArtifactRequestContext } from "./workspace-artifact-request";

/** Every desktop entry uses the same task identity and embedded content host. */
export function useDesktopWorkspaces() {
  const windows = useDesktopWindows();
  const { openWindow, reportWindowRoute } = windows;
  const openWorkspace = useCallback(
    (route: string, options: { state?: unknown; title?: string } = {}) => {
      const workspaceRoute = normalizeWorkspaceRoute(route);
      if (!workspaceRoute) return;
      const id = `agent-window:${uuid()}`;
      openWindow({
        id,
        workspaceRoute,
        threadId: threadIdForWorkspaceRoute(workspaceRoute),
        title: options.title ?? "工作台",
        url: resolveAgentAppUrl(workspaceRoute),
        content: (
          <EmbeddedAgentWorkspace
            initialRoute={workspaceRoute}
            initialState={options.state}
            onRouteChange={(location) => {
              reportWindowRoute(id, location, resolveAgentAppUrl(location));
            }}
          />
        ),
        integratedChrome: true,
      });
    },
    [openWindow, reportWindowRoute],
  );
  return {
    ...windows,
    windows: windows.windows.map((window) => ({
      ...window,
      content: (
        <WorkspaceArtifactRequestContext.Provider
          value={window.artifactRequest}
        >
          {window.content}
        </WorkspaceArtifactRequestContext.Provider>
      ),
    })),
    openWorkspace,
  };
}
