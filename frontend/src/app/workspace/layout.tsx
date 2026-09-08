import { Fragment, lazy, Suspense, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ListChecksIcon } from "lucide-react";

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
import { swallow } from "@/core/utils/log";
import { uuid } from "@/core/utils/uuid";
import { useWorkspaceShortcuts } from "@/core/shortcuts/use-global-shortcuts";
import { useI18n } from "@/core/i18n/hooks";
import { taskWorkspaceRoute } from "@/core/router/task-workspace-route";
import { preserveWorkbenchPresentation } from "@/core/router/desktop-workspace-route";
import { useActiveAgentId } from "@/core/agents/active";
import { workspacePresetForAgent } from "@/core/workspace/workspace-presets";
import { useWorkbenchAvailabilitySync } from "@/core/workbench/availability";
import { cn } from "@/lib/utils";
import { NasAlertNotifications } from "@/appliance/nas-alert-notifications";
import { SystemModelStatus } from "@/appliance/system-model-status";
import { TaskSpacePanel } from "@/appliance/task-space-panel";
import { useEchoTaskProjection } from "@/appliance/task-space";
import { ShellModeSwitch } from "@/components/retained-shell-routes";

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
  const workbenchPresentation =
    !embeddedInWindow &&
    !embeddedWorkspace &&
    searchParams.get("presentation") === "workbench";
  const currentSearch = searchParams.toString();
  const [taskSpaceOpen, setTaskSpaceOpen] = useState(false);
  const {
    projection: taskProjection,
    loading: taskProjectionLoading,
    error: taskProjectionError,
    refresh: refreshTaskProjection,
    takeover: takeoverTaskProjection,
    resumeExecution: resumeTaskProjection,
    decideApproval: decideTaskApproval,
  } = useEchoTaskProjection(workbenchPresentation);
  useWorkspaceShortcuts();
  useWorkbenchAvailabilitySync();
  useEvent(
    "task:new",
    (taskIdentity) => {
      navigate(
        preserveWorkbenchPresentation(
          taskWorkspaceRoute({
            agentId: taskIdentity?.agentId,
            workspacePath: taskIdentity?.workspacePath,
          }),
          currentSearch,
        ),
        {
          state: {
            taskNonce: uuid(),
            workspacePath: taskIdentity?.workspacePath,
          },
        },
      );
    },
    [currentSearch, navigate],
  );
  return (
    <Fragment>
      <NasAlertNotifications />
      {embeddedWorkspace ? (
        <SidebarProvider
          data-persona-theme={personaThemeId}
          className={cn(
            "persona-shell workspace-shell overflow-hidden bg-background",
            embeddedInWindow ? "h-full min-h-0" : "h-screen",
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
          <div className="min-h-0 min-w-0 flex-1 overflow-hidden">
            <WorkspaceRouteOutlet />
          </div>
        </SidebarProvider>
      ) : (
        <>
          <SidebarProvider
            data-persona-theme={personaThemeId}
            className={cn(
              "persona-shell workspace-shell overflow-hidden",
              embeddedInWindow ? "h-full min-h-0" : "h-screen",
            )}
            defaultOpen
            style={
              embeddedInWindow
                ? ({ "--sidebar-width-icon": "64px" } as React.CSSProperties)
                : electron
                  ? ({
                      paddingTop: ELECTRON_TITLE_BAR_HEIGHT,
                    } as React.CSSProperties)
                  : undefined
            }
          >
            <WorkspaceSidebar />
            <SidebarInset className="relative z-[1] flex min-h-0 min-w-0 flex-col overflow-hidden">
              {workbenchPresentation && (
                <header
                  aria-label="工作台系统状态"
                  className="flex shrink-0 items-center justify-between border-b px-3 py-1.5"
                >
                  <ShellModeSwitch />
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      aria-label="任务空间"
                      title="任务空间"
                      onClick={() => setTaskSpaceOpen(true)}
                      className="relative inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted-foreground transition hover:bg-muted hover:text-foreground"
                    >
                      <ListChecksIcon className="size-3.5" />
                      <span className="hidden sm:inline">任务空间</span>
                      {(taskProjection?.counts.waitingApproval ?? 0) > 0 && (
                        <span className="grid min-w-4 place-items-center rounded-full bg-amber-500 px-1 text-[9px] font-semibold leading-4 text-white">
                          {Math.min(taskProjection!.counts.waitingApproval, 99)}
                        </span>
                      )}
                    </button>
                    <SystemModelStatus
                      onOpenSettings={() =>
                        window.dispatchEvent(
                          new CustomEvent("echo:open-settings", {
                            detail: { tab: "models" },
                          }),
                        )
                      }
                    />
                  </div>
                </header>
              )}
              <StubResponseBannerHost />
              <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
                <WorkspaceRouteOutlet />
              </div>
            </SidebarInset>
          </SidebarProvider>
          <TaskSpacePanel
            open={taskSpaceOpen && workbenchPresentation}
            projection={taskProjection}
            loading={taskProjectionLoading}
            error={taskProjectionError}
            onClose={() => setTaskSpaceOpen(false)}
            onRefresh={refreshTaskProjection}
            onTakeover={takeoverTaskProjection}
            onResumeExecution={resumeTaskProjection}
            onApprovalDecision={decideTaskApproval}
            onOpenWorkspace={(task, artifact) => {
              setTaskSpaceOpen(false);
              navigate(
                preserveWorkbenchPresentation(
                  taskWorkspaceRoute({
                    threadId: task?.threadId ?? undefined,
                    agentId: task?.agentId ?? activeAgentId,
                    artifact,
                  }),
                  currentSearch,
                ),
              );
            }}
          />
          <Suspense fallback={null}>
            <CommandPalette />
          </Suspense>
        </>
      )}
    </Fragment>
  );
}
