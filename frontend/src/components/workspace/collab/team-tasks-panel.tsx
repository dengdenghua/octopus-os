import { useMemo, useState } from "react";
import {
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  ClipboardListIcon,
  Loader2Icon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  Trash2Icon,
  XCircleIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import { Progress } from "@/components/ui/progress";
import { useI18n } from "@/core/i18n/hooks";
import {
  useDeleteTeamTask,
  useRunTeamTask,
  useTeamTaskProcessTimeline,
  useTeamTasks,
  useUpdateTeamTask,
} from "@/core/team-tasks";
import type {
  TeamTask,
  TeamTaskProcessTimeline,
  TeamTaskProcessTimelineNode,
  TeamTaskStatus,
} from "@/core/team-tasks";
import type { Team } from "@/core/teams";
import { cn } from "@/lib/utils";

import {
  useOptionalCollab,
  type TeamTaskProgressEvent,
} from "./collab-provider";
import { CreateTaskDialog } from "./create-task-dialog";

interface TeamTasksPanelProps {
  roomId: string | null | undefined;
  team: Team | null;
  canManageTasks?: boolean;
}

export function TeamTasksPanel({
  roomId,
  team,
  canManageTasks = true,
}: TeamTasksPanelProps) {
  const { t } = useI18n();
  const { confirm, confirmDialog } = useConfirmDialog();
  const [filter, setFilter] = useState<"all" | TeamTaskStatus>("all");
  const [createOpen, setCreateOpen] = useState(false);
  const tasksQuery = useTeamTasks(roomId ?? null);
  const runTask = useRunTeamTask();
  const updateTask = useUpdateTeamTask();
  const deleteTask = useDeleteTeamTask();
  const collab = useOptionalCollab();

  const filters = useMemo<Array<{ id: "all" | TeamTaskStatus; label: string }>>(
    () => [
      { id: "all", label: t.taskBoard.all },
      { id: "pending", label: t.teamTasksPanel.statusPending },
      { id: "running", label: t.taskBoard.running },
      { id: "done", label: t.agentWorkbenchPages.statusDone },
      { id: "failed", label: t.agentWorkbenchPages.statusError },
    ],
    [t],
  );

  const statusMeta = useMemo<
    Record<
      TeamTaskStatus,
      {
        label: string;
        Icon: typeof CheckCircle2Icon;
        className: string;
      }
    >
  >(
    () => ({
      pending: {
        label: t.teamTasksPanel.statusPending,
        Icon: ClipboardListIcon,
        className: "border-warning/25 bg-warning/10 text-warning",
      },
      running: {
        label: t.taskBoard.running,
        Icon: Loader2Icon,
        className: "border-info/25 bg-info/10 text-info",
      },
      done: {
        label: t.agentWorkbenchPages.statusDone,
        Icon: CheckCircle2Icon,
        className: "border-success/25 bg-success/10 text-success",
      },
      failed: {
        label: t.agentWorkbenchPages.statusError,
        Icon: XCircleIcon,
        className: "border-destructive/30 bg-destructive/10 text-destructive",
      },
      cancelled: {
        label: t.taskBoard.paused,
        Icon: PauseIcon,
        className: "border-muted-foreground/25 bg-muted text-muted-foreground",
      },
    }),
    [t],
  );

  const tasks = useMemo(() => tasksQuery.data ?? [], [tasksQuery.data]);
  const latestEventByTask = useMemo(() => {
    const byTask = new Map<string, TeamTaskProgressEvent>();
    for (const event of collab?.taskEvents ?? []) {
      byTask.set(event.task_id, event);
    }
    return byTask;
  }, [collab?.taskEvents]);
  const visibleTasks = useMemo(
    () =>
      filter === "all" ? tasks : tasks.filter((task) => task.status === filter),
    [filter, tasks],
  );
  const runningCount = tasks.filter((task) => task.status === "running").length;
  const doneCount = tasks.filter((task) => task.status === "done").length;

  const handleRun = async (task: TeamTask) => {
    try {
      await runTask.mutateAsync({ taskId: task.id });
      toast.success(t.teamTasksPanel.toast.runStarted);
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t.teamTasksPanel.toast.runFailed,
      );
    }
  };

  const handleCancel = async (task: TeamTask) => {
    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        input: { status: "cancelled" },
      });
      toast.success(t.teamTasksPanel.toast.taskPaused);
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t.teamTasksPanel.toast.pauseFailed,
      );
    }
  };

  const handleDelete = async (task: TeamTask) => {
    if (
      !(await confirm({
        title: t.teamTasksPanel.deleteConfirmTitle,
        description: t.teamTasksPanel.deleteConfirmDescription(task.title),
        confirmLabel: t.common.delete,
      }))
    )
      return;
    try {
      await deleteTask.mutateAsync({ taskId: task.id });
      toast.success(t.teamTasksPanel.toast.taskDeleted);
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t.teamTasksPanel.toast.deleteFailed,
      );
    }
  };

  function formatTeamRole(role?: string | null): string | null {
    if (!role) return null;
    const normalized = role.replace(/^Role\./, "").toLowerCase();
    const labels: Record<string, string> = {
      planner: t.teamTasksPanel.roles.planner,
      researcher: t.teamTasksPanel.roles.researcher,
      generator: t.teamTasksPanel.roles.executor,
      implementer: t.teamTasksPanel.roles.executor,
      critic: t.teamTasksPanel.roles.critic,
      synthesizer: t.teamTasksPanel.roles.synthesizer,
      evaluator: t.teamTasksPanel.roles.evaluator,
    };
    return labels[normalized] ?? normalized;
  }

  function formatTeamTaskEvent(
    event: string,
    roleLabel: string | null,
  ): string | null {
    switch (event) {
      case "run_started":
        return t.teamTasksPanel.events.runStarted;
      case "team_role_start":
        return roleLabel
          ? t.teamTasksPanel.events.roleStarted(roleLabel)
          : t.teamTasksPanel.events.roleStarted();
      case "role_completed":
      case "team_role_end":
        return roleLabel
          ? t.teamTasksPanel.events.roleCompleted(roleLabel)
          : t.teamTasksPanel.events.roleCompleted();
      case "run_done":
        return t.teamTasksPanel.events.runDone;
      case "run_failed":
        return t.teamTasksPanel.events.runFailed;
      case "run_cancelled":
        return t.teamTasksPanel.events.runCancelled;
      default:
        return roleLabel
          ? t.teamTasksPanel.events.fallback(roleLabel)
          : t.teamTasksPanel.events.fallback();
    }
  }

  if (!roomId) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center px-5 text-center text-sm text-muted-foreground">
        {t.teamTasksPanel.emptyState}
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-background/70">
      <div className="shrink-0 border-b border-border-subtle px-3 py-2">
        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium text-foreground">
              {t.agentWorkbenchPages.reference.plans}
            </div>
            <div className="mt-0.5 text-xs text-muted-foreground">
              {t.teamTasksPanel.summary(tasks.length, runningCount, doneCount)}
            </div>
          </div>
          <Button
            size="sm"
            className="h-8 gap-1.5 rounded-md"
            disabled={!canManageTasks}
            onClick={() => setCreateOpen(true)}
          >
            <PlusIcon className="size-3.5" />
            {t.teamTasksPanel.newTask}
          </Button>
        </div>
        <div className="mt-2 flex gap-1 overflow-x-auto pb-0.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {filters.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setFilter(item.id)}
              className={cn(
                "h-7 shrink-0 rounded-md px-2 text-xs font-medium transition-colors",
                filter === item.id
                  ? "bg-foreground/10 text-foreground"
                  : "text-muted-foreground hover:bg-muted/55 hover:text-foreground",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {tasksQuery.isLoading ? (
          <div className="flex min-h-40 items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2Icon className="size-4 animate-spin" />
            {t.teamTasksPanel.loading}
          </div>
        ) : visibleTasks.length === 0 ? (
          <div className="flex min-h-48 items-center justify-center rounded-lg border border-dashed border-border-default bg-muted/15 px-4 text-center text-sm text-muted-foreground">
            {t.teamTasksPanel.emptyFilter}
          </div>
        ) : (
          <div className="space-y-2">
            {visibleTasks.map((task) => (
              <TaskRow
                key={task.id}
                task={task}
                team={team}
                taskEvent={latestEventByTask.get(task.id)}
                canManageTasks={canManageTasks}
                busy={
                  runTask.isPending ||
                  updateTask.isPending ||
                  deleteTask.isPending
                }
                onRun={() => void handleRun(task)}
                onCancel={() => void handleCancel(task)}
                onDelete={() => void handleDelete(task)}
              />
            ))}
          </div>
        )}
      </div>

      <CreateTaskDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        roomId={roomId}
        team={team}
      />
      {confirmDialog}
    </div>
  );

  function TaskRow({
    task,
    team,
    taskEvent,
    canManageTasks,
    busy,
    onRun,
    onCancel,
    onDelete,
  }: {
    task: TeamTask;
    team: Team | null;
    taskEvent?: TeamTaskProgressEvent;
    canManageTasks: boolean;
    busy: boolean;
    onRun: () => void;
    onCancel: () => void;
    onDelete: () => void;
  }) {
    const status = statusMeta[task.status] ?? statusMeta.pending;
    const StatusIcon = status.Icon;
    const progress = taskProgressValue(task, taskEvent);
    const assigneeLabels = assigneeNames(task, team);
    const artifactCount = task.produced_artifacts?.length ?? 0;
    const [showArtifacts, setShowArtifacts] = useState(false);
    const [showTimeline, setShowTimeline] = useState(false);
    const timelineQuery = useTeamTaskProcessTimeline(task.id, {
      enabled: showTimeline,
      refetchMs: showTimeline && task.status === "running" ? 1500 : false,
    });
    const roleLabel = formatTeamRole(taskEvent?.role);
    const liveStatus = taskEvent
      ? formatTeamTaskEvent(taskEvent.event, roleLabel)
      : null;

    return (
      <article className="rounded-lg border border-border-default bg-background/85 shadow-[var(--shadow-xs)]">
        <div className="flex items-start gap-2.5 px-3 py-2.5">
          <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
            <ClipboardListIcon className="size-4" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2">
              <h3 className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">
                {task.title}
              </h3>
              <Badge
                variant="outline"
                className={cn("h-6 gap-1", status.className)}
              >
                <StatusIcon
                  className={cn(
                    "size-3",
                    task.status === "running" && "animate-spin",
                  )}
                />
                {status.label}
              </Badge>
            </div>
            {task.description && (
              <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
                {task.description}
              </p>
            )}
            <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
              <span className="rounded-md bg-muted/60 px-1.5 py-0.5">
                {task.sop_template || t.teamTasksPanel.autoMatch}
              </span>
              {assigneeLabels.length > 0 && (
                <span className="rounded-md bg-muted/60 px-1.5 py-0.5">
                  {assigneeLabels.join("、")}
                </span>
              )}
              {artifactCount > 0 && (
                <button
                  type="button"
                  onClick={() => setShowArtifacts((v) => !v)}
                  className="flex items-center gap-0.5 rounded-md bg-success/10 px-1.5 py-0.5 text-success transition-colors hover:bg-success/20"
                >
                  {showArtifacts ? (
                    <ChevronDownIcon className="size-3" />
                  ) : (
                    <ChevronRightIcon className="size-3" />
                  )}
                  {t.teamTasksPanel.artifactCount(artifactCount)}
                </button>
              )}
              {task.status !== "pending" && (
                <button
                  type="button"
                  onClick={() => setShowTimeline((v) => !v)}
                  className="flex items-center gap-0.5 rounded-md bg-info/10 px-1.5 py-0.5 text-info transition-colors hover:bg-info/20"
                >
                  {showTimeline ? (
                    <ChevronDownIcon className="size-3" />
                  ) : (
                    <ChevronRightIcon className="size-3" />
                  )}
                  {t.teamTasksPanel.timeline.evidenceToggle}
                </button>
              )}
            </div>
          </div>
        </div>

        {showArtifacts && artifactCount > 0 && (
          <div className="space-y-1.5 border-t border-border-subtle px-3 py-2">
            {task.produced_artifacts.map((artifact, i) => {
              const a = artifact as Record<string, unknown>;
              const title = String(
                a.title ?? a.agent_id ?? a.type ?? `产出 ${i + 1}`,
              );
              const content = String(a.content ?? "");
              const failureLabel = String(a.failure_label ?? "");
              const ok = a.ok;
              return (
                <div
                  key={String(a.id ?? i)}
                  className="overflow-hidden rounded-md border border-border-default bg-muted/20"
                >
                  <div className="flex items-center gap-1.5 px-2 py-1 text-xs font-medium">
                    {ok === true && (
                      <CheckCircle2Icon className="size-3 shrink-0 text-success" />
                    )}
                    {ok === false && (
                      <XCircleIcon className="size-3 shrink-0 text-warning" />
                    )}
                    <span className="truncate">{title}</span>
                    {failureLabel && ok === false && (
                      <span className="shrink-0 rounded bg-warning/10 px-1.5 py-0.5 text-xs text-warning">
                        {failureLabel}
                      </span>
                    )}
                  </div>
                  {content && (
                    <pre className="max-h-48 overflow-auto border-t border-border-subtle px-2 py-1.5 text-xs leading-snug whitespace-pre-wrap break-words">
                      {content}
                    </pre>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {showTimeline && (
          <TeamTaskTimelinePreview
            timeline={timelineQuery.data ?? null}
            loading={timelineQuery.isLoading || timelineQuery.isFetching}
            error={timelineQuery.error}
            labels={t.teamTasksPanel.timeline}
          />
        )}

        {liveStatus && task.status === "running" && (
          <div className="px-3 pb-2 text-xs text-primary">
            {liveStatus}
            {taskEvent?.completed_roles != null &&
            taskEvent?.total_roles != null
              ? t.teamTasksPanel.rolesCompleted(
                  taskEvent.completed_roles,
                  taskEvent.total_roles,
                )
              : ""}
          </div>
        )}

        {(task.status === "running" || task.status === "done") && (
          <div className="px-3 pb-2">
            <Progress value={progress} className="h-1.5 bg-muted" />
          </div>
        )}

        <div className="flex items-center justify-end gap-1 border-t border-border-subtle px-2.5 py-1.5">
          {task.status === "running" ? (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 rounded-md px-2 text-xs"
              disabled={!canManageTasks || busy}
              onClick={onCancel}
            >
              <PauseIcon className="mr-1 size-3.5" />
              {t.backgroundTasks.pause}
            </Button>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 rounded-md px-2 text-xs"
              disabled={!canManageTasks || busy}
              onClick={onRun}
            >
              <PlayIcon className="mr-1 size-3.5" />
              {t.executionPanel.run}
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            className="h-7 rounded-md px-2 text-xs text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
            disabled={!canManageTasks || busy}
            onClick={onDelete}
          >
            <Trash2Icon className="mr-1 size-3.5" />
            {t.common.delete}
          </Button>
        </div>
      </article>
    );
  }
}

function TeamTaskTimelinePreview({
  timeline,
  loading,
  error,
  labels,
}: {
  timeline: TeamTaskProcessTimeline | null;
  loading: boolean;
  error: unknown;
  labels: {
    processCount: (count: number) => string;
    artifactCount: (count: number) => string;
    rawState: (included: boolean) => string;
    refreshing: string;
    empty: string;
  };
}) {
  const nodes = (timeline?.timeline ?? []).slice(-8);
  return (
    <div className="border-t border-border-subtle px-3 py-2">
      <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
        <span className="rounded-md bg-muted/60 px-1.5 py-0.5">
          {labels.processCount(timeline?.overview.event_count ?? 0)}
        </span>
        <span className="rounded-md bg-muted/60 px-1.5 py-0.5">
          {labels.artifactCount(timeline?.overview.artifact_count ?? 0)}
        </span>
        <span className="rounded-md bg-muted/60 px-1.5 py-0.5">
          {labels.rawState(Boolean(timeline?.safety.raw_messages_included))}
        </span>
        {loading && (
          <span className="inline-flex items-center gap-1 text-primary">
            <Loader2Icon className="size-3 animate-spin" />
            {labels.refreshing}
          </span>
        )}
      </div>
      {error ? (
        <div className="mt-2 rounded-md border border-destructive/25 bg-destructive/10 px-2 py-1.5 text-xs leading-5 text-destructive">
          {error instanceof Error ? error.message : String(error)}
        </div>
      ) : nodes.length === 0 ? (
        <div className="mt-2 rounded-md border border-dashed border-border-default bg-muted/15 px-2 py-3 text-center text-xs text-muted-foreground">
          {labels.empty}
        </div>
      ) : (
        <div className="mt-2 space-y-1.5">
          {nodes.map((node) => (
            <TimelineNodeRow key={node.id} node={node} />
          ))}
        </div>
      )}
    </div>
  );
}

function TimelineNodeRow({ node }: { node: TeamTaskProcessTimelineNode }) {
  return (
    <div
      className={cn(
        "grid grid-cols-[4.25rem_1fr] gap-2 rounded-md border px-2 py-1.5 text-xs leading-5",
        node.severity === "high"
          ? "border-destructive/25 bg-destructive/10"
          : node.lane === "artifact"
            ? "border-success/20 bg-success/10"
            : "border-border-default bg-muted/15",
      )}
    >
      <div className="min-w-0 text-muted-foreground">
        <div className="truncate font-mono">{node.lane}</div>
        <div className="truncate">{formatTimelineTime(node.ts)}</div>
      </div>
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="truncate font-medium text-foreground">
            {node.title || node.kind}
          </span>
          {node.status && (
            <span className="shrink-0 rounded bg-background/70 px-1 font-mono text-xs text-muted-foreground">
              {node.status}
            </span>
          )}
        </div>
        {node.summary && (
          <div className="mt-0.5 line-clamp-2 text-muted-foreground">
            {node.summary}
          </div>
        )}
      </div>
    </div>
  );
}

function formatTimelineTime(value: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function assigneeNames(task: TeamTask, team: Team | null): string[] {
  const byRef = new Map(
    (team?.members ?? []).map((member) => [
      member.name,
      member.display_name ?? member.name,
    ]),
  );
  return (task.assignees ?? [])
    .map((assignee) => byRef.get(assignee.ref) ?? assignee.ref)
    .filter(Boolean)
    .slice(0, 3);
}

function taskProgressValue(
  task: TeamTask,
  event?: TeamTaskProgressEvent,
): number {
  if (task.status === "done") return 100;
  if (task.status === "failed" || task.status === "cancelled") return 100;
  if (typeof event?.progress === "number") {
    return Math.max(0, Math.min(100, Math.round(event.progress * 100)));
  }
  if (task.status === "running") return 8;
  return 0;
}
