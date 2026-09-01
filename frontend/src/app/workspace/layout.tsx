import { Fragment, lazy, Suspense, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { Banner } from "@/components/ui/banner";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { WorkspaceSidebar } from "@/components/workspace/workspace-sidebar";
import { WorkspaceRouteOutlet } from "@/components/workspace/workspace-route-outlet";
import {
  ELECTRON_TITLE_BAR_HEIGHT,
  inElectron,
} from "@/components/electron-title-bar";
import {
  STUB_RESPONSE_EVENT,
  type StubResponseDetail,
} from "@/core/api/client";
import { useEvent } from "@/core/events";
import { useModuleRouteGuard } from "@/core/modules/use-module-route-guard";
import { PRIMARY_WORKSPACE_ROUTE } from "@/core/workspace/sidebar-routing";
import { swallow } from "@/core/utils/log";
import { uuid } from "@/core/utils/uuid";
import { useWorkspaceShortcuts } from "@/core/shortcuts/use-global-shortcuts";
import { useI18n } from "@/core/i18n/hooks";
import { taskWorkspaceRoute } from "@/core/router/task-workspace-route";
import { useActiveAgentId } from "@/core/agents/active";
import { workspacePresetForAgent } from "@/core/workspace/workspace-presets";
import { useWorkbenchAvailabilitySync } from "@/core/workbench/availability";
import { cn } from "@/lib/utils";

const CommandPalette = lazy(() =>
  import("@/components/workspace/command-palette").then((m) => ({
    default: m.CommandPalette,
  })),
);

function showStubResponseBanner(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return (
      window.localStorage?.getItem?.("echo.debug.showStubResponses") === "true"
    );
  } catch (e) {
    swallow(e);
    return false;
  }
}

function StubResponseBannerHost() {
  const { t } = useI18n();
  const [latest, setLatest] = useState<StubResponseDetail | null>(null);
  const showStubBanner = showStubResponseBanner();

  useEffect(() => {
    if (!showStubBanner) return;
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<StubResponseDetail>).detail;
      if (!detail?.method || !detail.path) return;
      setLatest(detail);
    };
    window.addEventListener(STUB_RESPONSE_EVENT, handler);
    return () => window.removeEventListener(STUB_RESPONSE_EVENT, handler);
  }, [showStubBanner]);

  if (!showStubBanner || !latest) return null;

  return (
    <div className="border-b bg-background/95 px-3 py-2 backdrop-blur">
      <Banner
        tone="warning"
        title={t.common.stubResponseTitle}
        onDismiss={() => setLatest(null)}
        className="rounded-lg"
      >
        {t.common.stubResponseDescription(latest.method, latest.path)}
      </Banner>
    </div>
  );
}

export default function WorkspaceLayout({
  embeddedInWindow = false,
}: {
  embeddedInWindow?: boolean;
}) {
  const electron = inElectron();
  const navigate = useNavigate();
  const activeAgentId = useActiveAgentId() ?? "general";
  const personaThemeId = workspacePresetForAgent(activeAgentId).themeId;
  const [searchParams] = useSearchParams();
  const embeddedDesignChat = searchParams.get("embedded") === "design";
  const embeddedApp = searchParams.get("embedded") === "app";
  const embeddedWorkspace = embeddedDesignChat || embeddedApp;
  useWorkspaceShortcuts();
  useWorkbenchAvailabilitySync();
  // A hidden module's route must also be unreachable by URL, not just absent
  // from the sidebar.
  useModuleRouteGuard(PRIMARY_WORKSPACE_ROUTE);
  useEvent(
    "task:new",
    (taskIdentity) => {
      navigate(
        taskWorkspaceRoute({
          agentId: taskIdentity?.agentId,
          workspacePath: taskIdentity?.workspacePath,
        }),
        {
          state: {
            taskNonce: uuid(),
            workspacePath: taskIdentity?.workspacePath,
          },
        },
      );
    },
    [navigate],
  );
  return (
    <Fragment>
      {embeddedWorkspace ? (
        <SidebarProvider
          data-persona-theme={personaThemeId}
          className={cn(
            "persona-shell workspace-shell overflow-hidden bg-background",
            embeddedInWindow ? "h-full" : "h-screen",
          )}
          defaultOpen={false}
          style={
            electron && embeddedApp && !embeddedInWindow
              ? ({
                  paddingTop: ELECTRON_TITLE_BAR_HEIGHT,
                } as React.CSSProperties)
              : undefined
          }
        >
          <div className="min-w-0 flex-1 overflow-hidden">
            <WorkspaceRouteOutlet />
          </div>
        </SidebarProvider>
      ) : (
        <>
          <SidebarProvider
            data-persona-theme={personaThemeId}
            className={cn(
              "persona-shell workspace-shell overflow-hidden",
              embeddedInWindow ? "h-full" : "h-screen",
            )}
            defaultOpen
            style={
              electron && !embeddedInWindow
                ? ({
                    paddingTop: ELECTRON_TITLE_BAR_HEIGHT,
                  } as React.CSSProperties)
                : undefined
            }
          >
            <WorkspaceSidebar />
            <SidebarInset className="relative z-[1] flex min-w-0 flex-col overflow-hidden">
              <StubResponseBannerHost />
              <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
                <WorkspaceRouteOutlet />
              </div>
            </SidebarInset>
          </SidebarProvider>
          <Suspense fallback={null}>
            <CommandPalette />
          </Suspense>
        </>
      )}
    </Fragment>
  );
}
