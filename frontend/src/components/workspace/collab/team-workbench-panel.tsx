import {
  BotIcon,
  ClipboardListIcon,
  FolderIcon,
  MonitorIcon,
  UsersIcon,
  XIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { FileTree } from "@/components/workspace/file-tree";
import { WorkstationSeat } from "@/components/workspace/workstation-seat";
import { WorkDirSelector } from "@/components/workspace/workdir-selector";
import { withAgentAvatarVersion } from "@/core/agents/avatar";
import { useI18n } from "@/core/i18n/hooks";
import type { Team } from "@/core/teams";
import { cn } from "@/lib/utils";

import { TeamRoster } from "./team-roster";
import { TeamTasksPanel } from "./team-tasks-panel";

export type TeamWorkbenchTabId = "tasks" | "workspace" | "members";

interface TeamWorkbenchPanelProps {
  activeTab: TeamWorkbenchTabId;
  onSelectTab: (tab: TeamWorkbenchTabId) => void;
  onClose?: () => void;
  roomId: string | null | undefined;
  team: Team | null;
  workDir: string;
  onWorkDirChange: (path: string) => void;
  currentParticipantId?: string;
  canManageTasks?: boolean;
  onMention?: (name: string) => void;
  className?: string;
}

export function TeamWorkbenchPanel({
  activeTab,
  onSelectTab,
  onClose,
  roomId,
  team,
  workDir,
  onWorkDirChange,
  currentParticipantId,
  canManageTasks = true,
  onMention,
  className,
}: TeamWorkbenchPanelProps) {
  const { t } = useI18n();

  const TABS: Array<{
    id: TeamWorkbenchTabId;
    label: string;
    Icon: typeof ClipboardListIcon;
  }> = [
    { id: "members", label: t.collab.workbench.tabMembers, Icon: UsersIcon },
    { id: "tasks", label: t.collab.workbench.tabTasks, Icon: ClipboardListIcon },
    { id: "workspace", label: t.collab.workbench.tabWorkspace, Icon: FolderIcon },
  ];

  return (
    <div
      data-testid="team-workbench-panel"
      className={cn(
        "flex size-full min-h-0 flex-col bg-[color:color-mix(in_oklch,var(--muted)_46%,var(--background))]",
        className,
      )}
    >
      <header className="relative shrink-0 border-b border-border-default bg-background/95 px-2 pt-2 sm:px-3">
        <div className="flex min-w-0 items-end gap-1.5 sm:gap-2">
          <div className="mb-1.5 hidden h-8 shrink-0 items-center gap-2 rounded-lg border border-border-default bg-background/85 px-2 text-xs font-medium text-muted-foreground shadow-[var(--shadow-xs)] min-[520px]:flex">
            <MonitorIcon className="size-4" />
            <span>{t.collab.workbench.title}</span>
          </div>
          <div
            data-testid="team-workbench-tabs"
            role="tablist"
            aria-label={t.collab.workbench.title}
            className="flex min-w-0 flex-1 items-end gap-0.5 overflow-x-auto pr-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          >
            {TABS.map(({ id, label, Icon }) => {
              const active = activeTab === id;
              return (
                <button
                  key={id}
                  type="button"
                  data-testid={`team-workbench-tab-${id}`}
                  role="tab"
                  aria-selected={active}
                  onClick={() => onSelectTab(id)}
                  className={cn(
                    "inline-flex h-9 max-w-[9rem] shrink-0 items-center gap-1 rounded-lg border border-transparent px-2 text-xs font-medium transition-all sm:max-w-[11rem] sm:gap-1.5 sm:px-3 sm:text-sm",
                    active
                      ? "-mb-px h-10 rounded-b-none border-border-default border-b-background bg-background text-foreground shadow-[var(--shadow-xs)]"
                      : "mb-1 text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                  )}
                >
                  <Icon
                    className={cn(
                      "size-4 shrink-0",
                      active ? "text-primary" : "text-muted-foreground/80",
                    )}
                  />
                  <span className="truncate">{label}</span>
                </button>
              );
            })}
          </div>
          {onClose && (
            <Button
              variant="ghost"
              size="icon"
              className="mb-1 size-8 shrink-0 rounded-lg border border-transparent text-muted-foreground hover:border-border-default hover:bg-muted/60 hover:text-foreground"
              onClick={onClose}
              title={t.collab.workbench.closeTitle}
            >
              <XIcon className="size-4" />
            </Button>
          )}
        </div>
        <TeamMachineRail
          team={team}
          currentParticipantId={currentParticipantId}
          onMention={onMention}
          onSelectMembers={() => onSelectTab("members")}
        />
        {activeTab === "workspace" && (
          <WorkspacePathBar
            workDir={workDir}
            onWorkDirChange={onWorkDirChange}
          />
        )}
      </header>

      <main className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background/70">
        {activeTab === "tasks" ? (
          <TeamTasksPanel
            roomId={roomId}
            team={team}
            canManageTasks={canManageTasks}
          />
        ) : activeTab === "workspace" ? (
          <WorkspacePage workDir={workDir} />
        ) : (
          <TeamRoster
            team={team}
            currentParticipantId={currentParticipantId}
            onMention={onMention}
          />
        )}
      </main>
    </div>
  );
}

function TeamMachineRail({
  team,
  currentParticipantId,
  onMention,
  onSelectMembers,
}: {
  team: Team | null;
  currentParticipantId?: string;
  onMention?: (name: string) => void;
  onSelectMembers: () => void;
}) {
  const { t } = useI18n();
  const humans = (team?.participants ?? []).filter(
    (participant) => participant.status !== "removed",
  );
  const agents = team?.members ?? [];
  if (agents.length === 0 && humans.length === 0) return null;

  return (
    <div className="mt-1.5 flex min-w-0 items-center gap-2 border-t border-border-subtle py-1.5">
      <UsersIcon className="size-3.5 shrink-0 text-muted-foreground" />
      <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {agents.map((agent) => {
          const name = agent.display_name ?? agent.name;
          const isLeader = team?.leaderId === agent.name;
          const rawAvatar =
            agent.avatar_url ??
            (agent.name.startsWith("local_")
              ? `/api/agents/${agent.name}/avatar`
              : undefined);
          const avatarSrc = rawAvatar
            ? withAgentAvatarVersion(rawAvatar)
            : undefined;
          return (
            <WorkstationSeat
              key={agent.name}
              name={name}
              avatar={agent.icon}
              avatarUrl={avatarSrc}
              avatarNode={
                <BotIcon
                  className="size-3.5 text-muted-foreground"
                  aria-hidden="true"
                />
              }
              showBotBadge
              fallbackInitial={name.charAt(0)}
              dotClassName="bg-muted-foreground/45"
              dotLabel={isLeader ? t.collab.workbench.leaderStandby : t.collab.workbench.standby}
              title={
                agent.description ||
                t.collab.workbench.memberNameWithRole(name, isLeader)
              }
              ariaLabel={`@${name}`}
              selected={isLeader}
              iconOnly
              iconCaption={isLeader ? t.collab.common.leader : undefined}
              onClick={() => {
                onSelectMembers();
                onMention?.(agent.name);
              }}
              className="shrink-0"
            />
          );
        })}
        {humans.map((participant) => {
          const isSelf = participant.id === currentParticipantId;
          const isOnline = participant.status === "active";
          const statusText = isOnline ? t.collab.common.online : t.collab.common.offline;
          return (
            <WorkstationSeat
              key={participant.id}
              name={participant.display_name}
              fallbackInitial={participant.display_name.charAt(0)}
              dotClassName={
                isOnline ? "bg-success" : "bg-muted-foreground/35"
              }
              dotLabel={statusText}
              title={`${participant.display_name} · ${statusText} · ${participant.role}`}
              ariaLabel={`${participant.display_name} · ${statusText}`}
              selected={isSelf}
              iconOnly
              iconCaption={isSelf ? "You" : undefined}
              onClick={onSelectMembers}
              className="shrink-0"
            />
          );
        })}
      </div>
    </div>
  );
}

function WorkspacePathBar({
  workDir,
  onWorkDirChange,
}: {
  workDir: string;
  onWorkDirChange: (path: string) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="mt-2 flex items-center gap-2 border-t border-border-subtle py-2">
      <div className="flex min-w-0 flex-1 items-center gap-2 rounded-lg border border-border-default bg-muted/25 px-2.5 py-1.5">
        <FolderIcon className="size-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="text-xs font-medium text-muted-foreground">
            {t.collab.workbench.currentWorkspace}
          </div>
          <div className="truncate font-mono text-xs text-foreground">
            {workDir || t.collab.workbench.noDirectorySelected}
          </div>
        </div>
      </div>
      <WorkDirSelector
        workDir={workDir}
        onWorkDirChange={onWorkDirChange}
        className="shrink-0"
      />
    </div>
  );
}

function WorkspacePage({ workDir }: { workDir: string }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 p-1">
        <FileTree workDir={workDir} className="h-full" />
      </div>
    </div>
  );
}
