import type { ReactNode } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import { TerminalPanel } from "../terminal-panel";
import { AgentDiffPage } from "../agent-workbench-pages";
import { WorkbenchEmptyPage } from "../agent-workbench-pages";
import type { AgentWorkbenchTabId, DiffEntry } from "../agent-workbench-utils";
import { WorkbenchTabHeader, type WorkbenchTab } from "./workbench-tab-header";
import type { AgentRunState } from "../agent-run-status";

export function EmptyShellView({
  mainButton,
  visibleTabs,
  workbenchTabs,
  closedTabs,
  effectiveActiveTab,
  onTabClick,
  onTabClose,
  onClose,
  visibleDiffEntries,
  threadId,
  inferredWorkDir,
  browserTabPage,
  personaWorkbenchPage,
  isLoading,
  className,
  machineRail,
}: {
  mainButton: {
    active: boolean;
    label: string;
    onClick: () => void;
    runState: AgentRunState;
    title: string;
  };
  visibleTabs: WorkbenchTab[];
  workbenchTabs: WorkbenchTab[];
  closedTabs: Set<AgentWorkbenchTabId>;
  effectiveActiveTab:
    | "agent"
    | "diff"
    | "terminal"
    | "browser"
    | "artifacts"
    | "workspace"
    | "design"
    | "project";
  onTabClick: (tabId: AgentWorkbenchTabId) => void;
  onTabClose: (tabId: AgentWorkbenchTabId) => void;
  onClose?: () => void;
  visibleDiffEntries: DiffEntry[];
  threadId?: string | null;
  inferredWorkDir?: string;
  browserTabPage: ReactNode;
  personaWorkbenchPage: ReactNode;
  isLoading?: boolean;
  className?: string;
  machineRail: ReactNode;
}) {
  const { t } = useI18n();
  const emptyEmbeddedPage =
    effectiveActiveTab === "diff" ? (
      <AgentDiffPage
        entries={visibleDiffEntries}
        onBackToSummary={() => onTabClick("agent")}
      />
    ) : effectiveActiveTab === "terminal" ? (
      <TerminalPanel
        sessionId={`agent-workbench-${threadId ?? "local"}`}
        cwd={inferredWorkDir}
        className="min-h-0 flex-1"
      />
    ) : effectiveActiveTab === "browser" ? (
      browserTabPage
    ) : effectiveActiveTab === "workspace" ? (
      personaWorkbenchPage
    ) : (
      <WorkbenchEmptyPage
        title={t.agentWorkbenchPanel.robot}
        description={
          isLoading
            ? t.agentWorkbenchPanel.startingRobotProcess
            : t.agentWorkbenchPanel.noRunningRobotProcess
        }
      />
    );

  return (
    <div
      className={cn(
        "flex size-full min-h-0 flex-col bg-[color:color-mix(in_oklch,var(--muted)_46%,var(--background))]",
        className,
      )}
    >
      <WorkbenchTabHeader
        mainButton={mainButton}
        visibleTabs={visibleTabs}
        workbenchTabs={workbenchTabs}
        closedTabs={closedTabs}
        effectiveActiveTab={effectiveActiveTab}
        onTabClick={onTabClick}
        onTabClose={onTabClose}
        onClose={onClose}
      />
      <section
        aria-label={t.sidebar.ariaAgentWorkbench}
        className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background/70"
      >
        {emptyEmbeddedPage}
      </section>
      {machineRail}
    </div>
  );
}
