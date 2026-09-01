import { lazy, type ReactElement, useEffect } from "react";
import { Navigate, Route, useLocation, useNavigate } from "react-router-dom";

import { emitOpenSettings } from "@/core/events";
import {
  loadAgentsPage,
  loadProjectsPage,
} from "@/core/navigation/workspace-route-preload";
import {
  WORKBENCH_BUILTIN_APPS,
  type WorkbenchBuiltinApp,
} from "@/core/workbench/apps";
import { RemoteWorkbenchSurface } from "@/core/workbench/remote-surface";

import WorkspaceLayout from "./layout";

function remoteWorkbenchApp(id: string): WorkbenchBuiltinApp {
  const app = WORKBENCH_BUILTIN_APPS.find(
    (candidate) => candidate.id === id && candidate.delivery === "remote",
  );
  if (!app) throw new Error(`Unknown remote workbench app: ${id}`);
  return app;
}

const COMMUNITY_APP = remoteWorkbenchApp("community");
const INTELLIGENCE_APP = remoteWorkbenchApp("intelligence");
const DESIGN_APP = remoteWorkbenchApp("design");
const NARRATIVE_APP = remoteWorkbenchApp("narrative");
const PAPER_TRADING_APP = remoteWorkbenchApp("paper-trading");
const EVOLUTION_APP = remoteWorkbenchApp("evolution");

function StorageRedirect() {
  const search = window.location.hash.includes("?")
    ? window.location.hash.slice(window.location.hash.indexOf("?"))
    : "?surface=company";
  return <Navigate to={`/workspace/storage${search}`} replace />;
}

function HubAssetRedirect({ tab }: { tab: "plugins" | "skills" }) {
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  params.set("surface", "chat");
  params.set("tab", tab);
  return <Navigate to={`/workspace/agents?${params.toString()}`} replace />;
}

function RouteTransition() {
  return (
    <div
      role="status"
      aria-live="polite"
      className="grid size-full min-h-48 place-items-center bg-background text-sm text-muted-foreground"
    >
      正在打开工作区…
    </div>
  );
}

function SettingsRoute() {
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const section = params.get("section");
    const embedded = params.get("embedded");
    const target = embedded
      ? `/workspace/realtime/new?embedded=${encodeURIComponent(embedded)}`
      : "/workspace/realtime/new";
    navigate(target, { replace: true });
    const handle = window.setTimeout(() => {
      emitOpenSettings(section ?? undefined);
    }, 0);
    return () => window.clearTimeout(handle);
  }, [location.search, navigate]);

  return <RouteTransition />;
}

function EmbeddedBrowserRoute({ onOpen }: { onOpen: () => void }) {
  useEffect(() => {
    onOpen();
  }, [onOpen]);
  return <RouteTransition />;
}

const LEGACY_REDIRECTS = {
  mobile: "/workspace/computer",
  store: "/workspace/agents?surface=chat",
  replay: "/workspace/observability",
  workflows: "/workspace/agents?surface=chat&tab=skills",
} as const;

const ChatPage = lazy(() => import("./realtime/[thread_id]/page"));
const TeamJoinPage = lazy(() => import("./team/join/page"));
const ComputerPage = lazy(() => import("./computer/page"));
const DesktopOrganizerPage = lazy(() => import("./desktop-organizer/page"));
const AgentsPage = lazy(loadAgentsPage);
const AgentsNewPage = lazy(() => import("./agents/new/page"));
const ChannelsPage = lazy(() => import("./channels/page"));
const ArchitecturePage = lazy(() => import("./architecture/page"));
const ObservabilityPage = lazy(() => import("./observability/page"));
const KnowledgePage = lazy(() => import("./knowledge/page"));
const StoragePage = lazy(() => import("./storage/page"));
const ProjectsPage = lazy(loadProjectsPage);
const WorkspaceWebAppPage = lazy(() => import("./web-app/page"));
const ReflexMonitorPage = lazy(() => import("./reflex/page"));
const ReflexEditorPage = lazy(() => import("./reflex/edit/page"));

export type WorkspaceRouteOptions = {
  embeddedInWindow?: boolean;
  onOpenBrowser?: () => void;
};

/**
 * One canonical workspace route tree for both the top-level Agent UI and the
 * Echo desktop window. Keeping the route elements shared prevents the desktop
 * surface from drifting into a second, reduced Agent frontend.
 */
export function createWorkspaceRoute({
  embeddedInWindow = false,
  onOpenBrowser,
}: WorkspaceRouteOptions = {}): ReactElement {
  return (
    <Route
      path="/workspace"
      element={<WorkspaceLayout embeddedInWindow={embeddedInWindow} />}
    >
      <Route index element={<Navigate to="realtime/new" replace />} />
      <Route
        path="realtime"
        element={<Navigate to="/workspace/realtime/new" replace />}
      />
      <Route path="realtime/:threadId" element={<ChatPage />} />
      <Route path="team/join" element={<TeamJoinPage />} />
      <Route
        path="browser"
        element={
          embeddedInWindow && onOpenBrowser ? (
            <EmbeddedBrowserRoute onOpen={onOpenBrowser} />
          ) : (
            <Navigate to="/browser" replace />
          )
        }
      />
      <Route path="computer" element={<ComputerPage />} />
      <Route path="desktop-organizer" element={<DesktopOrganizerPage />} />
      <Route
        path="mobile"
        element={<Navigate to={LEGACY_REDIRECTS.mobile} replace />}
      />
      <Route path="settings" element={<SettingsRoute />} />
      <Route
        path="mcp"
        element={<Navigate to="/workspace/settings?section=tools" replace />}
      />
      <Route path="agents" element={<AgentsPage />} />
      <Route path="agents/new" element={<AgentsNewPage />} />
      <Route path="skills" element={<HubAssetRedirect tab="skills" />} />
      <Route
        path="community"
        element={<RemoteWorkbenchSurface app={COMMUNITY_APP} />}
      />
      <Route path="plugins" element={<HubAssetRedirect tab="plugins" />} />
      <Route
        path="store"
        element={<Navigate to={LEGACY_REDIRECTS.store} replace />}
      />
      <Route path="channels" element={<ChannelsPage />} />
      <Route path="architecture" element={<ArchitecturePage />} />
      <Route path="observability" element={<ObservabilityPage />} />
      <Route
        path="intelligence"
        element={<RemoteWorkbenchSurface app={INTELLIGENCE_APP} />}
      />
      <Route path="knowledge" element={<KnowledgePage />} />
      <Route path="storage" element={<StoragePage />} />
      <Route path="nas" element={<StorageRedirect />} />
      <Route path="database" element={<StorageRedirect />} />
      <Route
        path="evolution"
        element={<RemoteWorkbenchSurface app={EVOLUTION_APP} />}
      />
      <Route path="projects" element={<ProjectsPage />} />
      <Route
        path="design"
        element={<RemoteWorkbenchSurface app={DESIGN_APP} />}
      />
      <Route
        path="narrative"
        element={<RemoteWorkbenchSurface app={NARRATIVE_APP} />}
      />
      <Route
        path="paper-trading"
        element={<RemoteWorkbenchSurface app={PAPER_TRADING_APP} />}
      />
      <Route path="web-app" element={<WorkspaceWebAppPage />} />
      <Route
        path="replay"
        element={<Navigate to={LEGACY_REDIRECTS.replay} replace />}
      />
      <Route
        path="workflows"
        element={<Navigate to={LEGACY_REDIRECTS.workflows} replace />}
      />
      <Route path="reflex" element={<ReflexMonitorPage />} />
      <Route path="reflex/edit" element={<ReflexEditorPage />} />
      <Route
        path="diagnostics"
        element={<ObservabilityPage initialTab="diagnostics" />}
      />
    </Route>
  );
}
