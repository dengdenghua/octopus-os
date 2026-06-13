import { lazy, Suspense, useEffect } from "react";
import { Navigate, Route, Routes, useParams } from "react-router-dom";

import { ProtectedRoute } from "@/components/auth/protected-route";
import { ErrorBoundary } from "@/components/ui/error-boundary";

/**
 * Any direct link to ``/realtime/:threadId`` (old bookmarks, external
 * deep-links, historical emails) gets redirected into the workspace
 * shell at ``/workspace/realtime/:threadId``. The in-app migration
 * banner that used to point here is gone now that every workspace
 * route already runs over the WebSocket.
 */
function RealtimeRedirect() {
  const { threadId } = useParams<{ threadId: string }>();
  if (!threadId) return <Navigate to="/realtime" replace />;
  return <Navigate to={`/workspace/realtime/${threadId}`} replace />;
}

function LegacyCodeRedirect() {
  const { threadId } = useParams<{ threadId: string }>();
  const target =
    !threadId || threadId === "new"
      ? "/workspace/realtime/new"
      : `/workspace/realtime/${threadId}`;
  return <HashRedirect to={target} />;
}

function HashRedirect({ to }: { to: string }) {
  useEffect(() => {
    window.location.replace(`${window.location.pathname}#${to}`);
  }, [to]);
  return null;
}

const LoginPage = lazy(() => import("./app/login/page"));
const RegisterPage = lazy(() => import("./app/register/page"));
const AboutPage = lazy(() => import("./app/about/page"));
const DesktopPage = lazy(() => import("./app/desktop/page"));
const TopBrowserPage = lazy(() => import("./app/browser/page"));
const PluginsPage = lazy(() => import("./app/plugins/page"));

const WorkspaceLayout = lazy(() => import("./app/workspace/layout"));
const ChatPage = lazy(() => import("./app/workspace/chats/[thread_id]/page"));
const TeamIndexPage = lazy(() => import("./app/workspace/team/page"));
const TeamNewPage = lazy(() => import("./app/workspace/team/new/page"));
const TeamJoinPage = lazy(() => import("./app/workspace/team/join/page"));
const TeamPage = lazy(() => import("./app/workspace/team/[thread_id]/page"));
const BrowserPage = lazy(() => import("./app/workspace/browser/page"));
const ComputerPage = lazy(() => import("./app/workspace/computer/page"));
const DesktopOrganizerPage = lazy(
  () => import("./app/workspace/desktop-organizer/page"),
);
const MobilePage = lazy(() => import("./app/workspace/mobile/page"));
const McpPage = lazy(() => import("./app/workspace/mcp/page"));
const AgentsPage = lazy(() => import("./app/workspace/agents/page"));
const AgentsNewPage = lazy(() => import("./app/workspace/agents/new/page"));
const SkillsPage = lazy(() => import("./app/workspace/skills/page"));
// /workspace/store now redirects to /workspace/agents (HR/agent market).
// The StorePage component is unused but the file remains for browser/page.tsx
// which still imports UnifiedStoreOverlay from the same module.
const ChannelsPage = lazy(() => import("./app/workspace/channels/page"));
const ArchitecturePage = lazy(
  () => import("./app/workspace/architecture/page"),
);
// Workspace-scoped observability surface: focused tabs for swarm
// sub-agent tracing, blackboard snapshot, journal stream, 6-producer
// regeneration summary, hemolymph compose-budget meter, and per-task
// cost.
const ObservabilityPage = lazy(
  () => import("./app/workspace/observability/page"),
);
// Previously orphaned: implemented under ``src/app/workspace/`` and
// linked from the sidebar (``workspace-sidebar.tsx``) but never
// wired into this router. Clicks fell through to the ``*`` catch-all
// and bounced the user to landing. Fixed by registering them here.
const IntelligencePage = lazy(
  () => import("./app/workspace/intelligence/page"),
);
const KnowledgePage = lazy(() => import("./app/workspace/knowledge/page"));
const EvolutionPage = lazy(() => import("./app/workspace/evolution/page"));
// Reflex monitor + YAML editor · ports the inline-HTML
// /admin/reflex pages into the workspace shell so they pick up
// theming + sidebar nav. See app/workspace/reflex/page.tsx.
const ReflexMonitorPage = lazy(() => import("./app/workspace/reflex/page"));
const ReflexEditorPage = lazy(() => import("./app/workspace/reflex/edit/page"));
// Realtime thread surface — the long-term JSON-RPC WebSocket UI that
// replaces the SSE-based chat path. Lives outside the /workspace shell
// on purpose: this route intentionally has zero legacy chrome, so it's
// the shortest possible end-to-end path from WebSocket envelope to
// rendered item. Kept mountable at /realtime so developers can iterate
// without touching the workspace layout.
const RealtimeIndexPage = lazy(() => import("./app/realtime/page"));

function PageLoading() {
  return (
    <div className="flex h-screen items-center justify-center">
      <div className="text-muted-foreground text-sm">Loading...</div>
    </div>
  );
}

export function AppRouter() {
  return (
    <ErrorBoundary>
      <Suspense fallback={<PageLoading />}>
        <Routes>
          <Route path="/" element={<LoginPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/about" element={<AboutPage />} />

          <Route element={<ProtectedRoute />}>
            {/* Implementation note. */}
            <Route path="/desktop" element={<DesktopPage />} />
            <Route path="/browser" element={<TopBrowserPage />} />
            <Route path="/plugins" element={<PluginsPage />} />

            {/* Realtime *index* page (no thread id) — list of threads.
                Stays outside ``/workspace`` because it's a thin index
                without a chat shell. */}
            <Route path="/realtime" element={<RealtimeIndexPage />} />
            {/* Redirect old /realtime/:id bookmarks into the workspace
                shell. Inside the shell every route already runs over the
                WebSocket, so this is a backwards-compat helper — not a
                transport toggle. */}
            <Route path="/realtime/:threadId" element={<RealtimeRedirect />} />
            <Route
              path="/workspace/swarm"
              element={<HashRedirect to="/workspace/realtime/new" />}
            />

            <Route path="/workspace" element={<WorkspaceLayout />}>
              <Route index element={<Navigate to="realtime/new" replace />} />
              {/* Realtime per-thread page — hosted INSIDE the workspace
                  layout so it gets the WorkspaceSidebar and the
                  SidebarProvider that the chat shell depends on. */}
              <Route
                path="realtime"
                element={<Navigate to="/workspace/realtime/new" replace />}
              />
              <Route path="realtime/:threadId" element={<ChatPage />} />
              <Route path="chats/:threadId" element={<ChatPage />} />
              <Route
                path="code"
                element={<HashRedirect to="/workspace/realtime/new" />}
              />
              <Route
                path="code/new"
                element={<HashRedirect to="/workspace/realtime/new" />}
              />
              <Route path="code/:threadId" element={<LegacyCodeRedirect />} />
              <Route path="team" element={<TeamIndexPage />} />
              <Route path="team/new" element={<TeamNewPage />} />
              <Route path="team/join" element={<TeamJoinPage />} />
              <Route path="team/:threadId" element={<TeamPage />} />
              <Route path="browser" element={<BrowserPage />} />
              <Route path="computer" element={<ComputerPage />} />
              <Route
                path="desktop-organizer"
                element={<DesktopOrganizerPage />}
              />
              <Route path="mobile" element={<MobilePage />} />
              <Route path="mcp" element={<McpPage />} />
              <Route
                path="agents/:agentName/chats/:threadId"
                element={<ChatPage />}
              />
              <Route path="agents" element={<AgentsPage />} />
              <Route path="agents/new" element={<AgentsNewPage />} />
              <Route path="skills" element={<SkillsPage />} />
              <Route path="plugins" element={<PluginsPage />} />
              <Route
                path="store"
                element={<Navigate to="/workspace/agents" replace />}
              />
              <Route path="channels" element={<ChannelsPage />} />
              <Route path="architecture" element={<ArchitecturePage />} />
              <Route path="observability" element={<ObservabilityPage />} />
              {/* Sidebar-linked pages · see workspace-sidebar.tsx
                  TOOL_ITEMS / ADVANCED_ITEMS. Fully implemented on
                  disk; were not registered here until now. */}
              <Route path="intelligence" element={<IntelligencePage />} />
              <Route
                path="swarm"
                element={<HashRedirect to="/workspace/realtime/new" />}
              />
              <Route path="knowledge" element={<KnowledgePage />} />
              <Route path="evolution" element={<EvolutionPage />} />
              <Route path="reflex" element={<ReflexMonitorPage />} />
              <Route path="reflex/edit" element={<ReflexEditorPage />} />
              <Route
                path="diagnostics"
                element={<ObservabilityPage initialTab="diagnostics" />}
              />
            </Route>
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </ErrorBoundary>
  );
}
