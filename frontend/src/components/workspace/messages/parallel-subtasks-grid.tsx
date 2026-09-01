import { useState } from "react";
import { memo } from "react";
import { DotProgress } from "@/components/workspace/swarm/dot-progress";
import { emitAgentWorkbenchFocus } from "@/components/workspace/agent-workbench-events";
import { useI18n } from "@/core/i18n/hooks";
import { useSubtask, useSubtaskContext } from "@/core/tasks/context";
import {
  isSubtaskActive,
  type Subtask,
  type SubtaskStatus,
} from "@/core/tasks/types";
import { cn } from "@/lib/utils";
import {
  agentRunHue,
  agentRunPanelClass,
  agentRunStatusLightPulseClass,
} from "../agent-run-status";
import { subtaskProgress, subtaskProgressPercent, subtaskRunState } from "./subtask-status-ui";
import { friendlyRoleName } from "../agent-workbench-pages";
import { SubagentDetailsPanel } from "./subagent-details-panel";
import {
  CheckCircleIcon,
  Loader2Icon,
  PauseCircleIcon,
  XCircleIcon,
  ClockIcon,
  BanIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  ArrowRightIcon,
  FileIcon,
  RefreshCwIcon,
} from "lucide-react";

/**
 * Format duration in milliseconds to human-readable string
 */
function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  if (minutes < 60) {
    return remainingSeconds > 0 ? `${minutes}m ${remainingSeconds}s` : `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
}

function getStatusIcon(status: SubtaskStatus) {
  if (status === "completed")
    return <CheckCircleIcon className="size-3 text-success" />;
  if (status === "failed")
    return <XCircleIcon className="size-3 text-destructive" />;
  if (status === "cancelled")
    return <BanIcon className="size-3 text-warning" />;
  if (status === "timed_out")
    return <ClockIcon className="size-3 text-destructive" />;
  if (status === "pending")
    return <PauseCircleIcon className="size-3 text-warning" />;
  if (isSubtaskActive(status))
    return (
      <Loader2Icon className="size-3 animate-spin text-success" />
    );
  return null;
}

const MiniSubtaskRow = memo(function MiniSubtaskRow({
  taskId,
  isLoading: _isLoading,
  onClick,
  onDetailsClick,
  compact,
}: {
  taskId: string;
  isLoading: boolean;
  onClick?: () => void;
  onDetailsClick?: () => void;
  compact?: boolean;
}) {
  const task = useSubtask(taskId);
  const { t } = useI18n();
  const [showIdentity, setShowIdentity] = useState(false);

  if (!task) return null;

  const rawLabel = t.subagents[task.status as keyof typeof t.subagents];
  const statusLabel = typeof rawLabel === "string" ? rawLabel : task.status;
  const progress = subtaskProgress(task);
  const percent = subtaskProgressPercent(task);
  const runState = subtaskRunState(task.status);
  const progressHue = agentRunHue(runState);
  const isActive = runState === "running";
  const isTerminal =
    task.status === "completed" ||
    task.status === "failed" ||
    task.status === "cancelled" ||
    task.status === "timed_out";
  const roleName = friendlyRoleName(task.role ?? task.subagent_type);

  const isFailedLike = task.status === "failed" || task.status === "timed_out";
  const previewId = `subtask-preview-${task.id}`;
  // Terminal rows fill the bar; a real percent sets an exact width;
  // pending shows a sliver; running without real progress gets an
  // indeterminate bar via classes (no width claim).
  const barWidth = isTerminal
    ? "100%"
    : percent !== null
      ? `${Math.max(4, percent)}%`
      : task.status === "pending"
        ? "8%"
        : undefined;

  const handleClick = () => {
    if (onClick) {
      onClick();
      return;
    }
    emitAgentWorkbenchFocus({
      agentId: task.id,
      tab: "agent",
      view: "screen",
    });
  };
  const handleToggleIdentity = () => {
    setShowIdentity((v) => !v);
  };

  return (
    <div className="group/subtask-row relative">
      {/* The row is a div with a stretched primary-action button so the
          identity toggle can be a sibling <button> — button-in-button is
          invalid HTML and breaks keyboard/screen-reader semantics. */}
      <div
        className={cn(
          "relative flex w-full items-center gap-2 rounded-lg border text-left text-xs transition-all",
          compact ? "px-2 py-1.5" : "px-3 py-2",
          runState === "running"
            ? agentRunPanelClass("running")
            : "border-border bg-muted/30",
          "cursor-pointer hover:bg-muted/50",
        )}
      >
        <button
          type="button"
          onClick={handleClick}
          // Suppress mouse-click focus so group-focus-within doesn't pin
          // the hover preview open after a click; Tab focus is unaffected.
          onMouseDown={(e) => e.preventDefault()}
          aria-label={task.name ?? task.description}
          aria-describedby={previewId}
          className="absolute inset-0 cursor-pointer rounded-lg"
        />
        {task.avatarEmoji && (
          <span
            className={cn(
              "flex shrink-0 items-center justify-center rounded-lg",
              compact ? "size-5 text-xs" : "size-6 text-xs"
            )}
            style={
              task.hue != null
                ? { background: `hsl(${task.hue} 70% 92%)` }
                : undefined
            }
          >
            {task.avatarEmoji}
          </span>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            {getStatusIcon(task.status)}
            <span className="truncate font-medium">
              {task.name ?? task.description}
            </span>
            {!compact && (
              <span className="shrink-0 rounded bg-muted/50 px-1 py-0.5 text-xs font-medium text-muted-foreground">
                {roleName}
              </span>
            )}
          </div>
          {!compact && (
            <div className="text-muted-foreground mt-0.5 truncate">
              {task.description}
            </div>
          )}
          {/* 实时进度条 */}
          <div className={cn("flex items-center gap-1.5", compact ? "mt-1" : "mt-1.5")}>
            <div className="h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-muted/60">
              <div
                className={cn(
                  "h-full rounded-full transition-all duration-slow",
                  isFailedLike
                    ? "bg-destructive dark:bg-destructive"
                    : task.status === "cancelled"
                      ? "bg-warning dark:bg-warning"
                      : task.status === "completed"
                        ? "bg-success dark:bg-success"
                        : isActive
                          ? "bg-success dark:bg-success"
                          : "bg-muted-foreground/40",
                  isActive && percent === null && "w-2/5 animate-pulse",
                )}
                style={barWidth ? { width: barWidth } : undefined}
              />
            </div>
            {percent !== null ? (
              <span className="shrink-0 font-mono text-xs leading-none tabular-nums text-success">
                {percent}%
              </span>
            ) : isTerminal && task.status !== "completed" ? (
              <span
                className={cn(
                  "shrink-0 text-xs leading-none",
                  task.status === "cancelled"
                    ? "text-warning"
                    : "text-destructive",
                )}
              >
                {statusLabel}
              </span>
            ) : null}
            {/* 迭代次数 */}
            {task.iterationCount && task.iterationCount > 0 && (
              <span className="shrink-0 text-xs text-muted-foreground">
                · {task.iterationCount}x
              </span>
            )}
            {/* 文件修改数 */}
            {task.filesTouched && task.filesTouched.length > 0 && (
              <span className="shrink-0 flex items-center gap-0.5 text-xs text-muted-foreground">
                · <FileIcon className="size-3" /> {task.filesTouched.length}
              </span>
            )}
            {/* 角色说明 toggle — stacked above the stretched button */}
            <button
              type="button"
              onClick={handleToggleIdentity}
              aria-expanded={showIdentity}
              className="relative z-10 shrink-0 rounded px-1 py-0.5 text-xs font-medium text-muted-foreground/70 transition-colors hover:bg-muted/60 hover:text-foreground"
              title={t.agentWorkbenchPages.roleDescription}
            >
              {showIdentity
                ? t.agentWorkbenchPages.collapse
                : t.agentWorkbenchPages.roleDescription}
            </button>
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <span className="font-mono text-xs leading-none text-foreground">
            {task.id.slice(-2).toUpperCase()}
          </span>
          <DotProgress
            progress={progress}
            hue={progressHue}
            cols={16}
            rows={3}
            className={cn(agentRunStatusLightPulseClass(runState))}
          />
        </div>
      </div>
      {showIdentity && (
        <div className="mt-1.5">
          <AgentIdentityCard task={task} onClose={() => setShowIdentity(false)} />
        </div>
      )}
      <SubtaskHoverPreview
        task={task}
        statusLabel={statusLabel}
        id={previewId}
        onDetailsClick={onDetailsClick}
      />
    </div>
  );
});

export const SubtaskHoverPreview = memo(function SubtaskHoverPreview({
  task,
  statusLabel,
  id,
  onDetailsClick,
}: {
  task: Subtask;
  statusLabel: string;
  id?: string;
  onDetailsClick?: () => void;
}) {
  const { t } = useI18n();
  const body =
    task.prompt ||
    task.description ||
    task.result ||
    t.message.noTaskDescription;
  const isCompleted = task.status === "completed";
  const isFailedLike = task.status === "failed" || task.status === "timed_out";
  const isTerminal =
    isCompleted || isFailedLike || task.status === "cancelled";
  const isActive = isSubtaskActive(task.status);
  const percent = subtaskProgressPercent(task);
  const barWidth = isTerminal
    ? "100%"
    : percent !== null
      ? `${Math.max(4, percent)}%`
      : task.status === "pending"
        ? "8%"
        : undefined;
  const handleViewProcess = (e: React.MouseEvent) => {
    e.stopPropagation();
    emitAgentWorkbenchFocus({ agentId: task.id, tab: "agent", view: "screen" });
  };
  return (
    // The pt-2 padding doubles as a hover bridge: without it there is an
    // 8px dead zone between the row and the card where :hover drops.
    // group-focus-within keeps the card open while its buttons are tabbed.
    <div
      className="pointer-events-auto absolute left-8 top-full z-40 hidden w-[min(42rem,calc(100vw-5rem))] pt-2 group-focus-within/subtask-row:block group-hover/subtask-row:block"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="rounded-lg border border-border-default bg-background p-4 text-left shadow-sm">
        <div className="flex items-start gap-3">
          <span className="flex size-14 shrink-0 items-center justify-center rounded-full border border-border-default bg-muted/35 text-2xl">
            {task.avatarEmoji ?? "🤖"}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <div className="truncate text-lg font-semibold text-foreground">
                  {task.name ?? task.description}
                </div>
                <div className="truncate text-sm text-muted-foreground">
                  {task.role ?? task.subagent_type ?? t.message.assistant}
                </div>
              </div>
              <span className="font-mono text-sm text-foreground">
                {task.id.slice(-2).toUpperCase()}
              </span>
            </div>
            {/* The row button's aria-describedby targets just this meta
                line — describing the whole card would make screen readers
                read the full prompt for every row. */}
            <div
              id={id}
              className="mt-2 flex items-center gap-2 text-xs text-muted-foreground"
            >
              <span>{statusLabel}</span>
              <span>·</span>
              <span>{t.message.processRecords(task.messages?.length ?? 0)}</span>
              {task.tokenUsed !== undefined && (
                <>
                  <span>·</span>
                  <span>{task.tokenUsed.toLocaleString()} tokens</span>
                </>
              )}
            </div>
            {/* 进度条 */}
            <div className="mt-2 flex items-center gap-2">
              <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-muted/60">
                <div
                  className={cn(
                    "h-full rounded-full transition-all duration-slow",
                    isFailedLike
                      ? "bg-destructive dark:bg-destructive"
                      : task.status === "cancelled"
                        ? "bg-warning dark:bg-warning"
                        : isCompleted
                          ? "bg-success dark:bg-success"
                          : task.status === "pending"
                            ? "bg-muted-foreground/40"
                            : "bg-success dark:bg-success",
                    isActive && percent === null && "w-2/5 animate-pulse",
                  )}
                  style={barWidth ? { width: barWidth } : undefined}
                />
              </div>
              {percent !== null && (
                <span className="shrink-0 font-mono text-xs font-medium tabular-nums text-success">
                  {percent}%
                </span>
              )}
            </div>
            {/* 额外的统计信息 */}
            {(task.iterationCount || task.filesTouched?.length || task.duration) && (
              <div className="mt-3 flex items-center gap-4 text-xs">
                {task.iterationCount && task.iterationCount > 0 && (
                  <div className="flex items-center gap-1.5">
                    <RefreshCwIcon className="size-3 text-muted-foreground" />
                    <span className="text-muted-foreground">
                      {task.iterationCount} {t.subagents.iterations}
                    </span>
                  </div>
                )}
                {task.filesTouched && task.filesTouched.length > 0 && (
                  <div className="flex items-center gap-1.5">
                    <FileIcon className="size-3 text-muted-foreground" />
                    <span className="text-muted-foreground">
                      {task.filesTouched.length} {t.subagents.filesModified}
                    </span>
                  </div>
                )}
                {task.duration && (
                  <div className="flex items-center gap-1.5">
                    <ClockIcon className="size-3 text-muted-foreground" />
                    <span className="text-muted-foreground">
                      {formatDuration(task.duration)}
                    </span>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
        <div className="mt-4 max-h-64 overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-muted/35 p-3 text-sm leading-6 text-foreground">
          {body}
        </div>
        <div className="mt-3 flex items-center gap-2">
          <button
            type="button"
            onClick={handleViewProcess}
            className="inline-flex h-8 items-center gap-1.5 rounded-md bg-foreground px-3 text-xs font-medium text-background transition-opacity hover:opacity-90"
          >
            <ArrowRightIcon className="size-3" />
            {t.message.viewProcess}
          </button>
          {onDetailsClick && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onDetailsClick();
              }}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-background px-3 text-xs font-medium transition-colors hover:bg-muted"
            >
              {t.subagents.viewDetails}
            </button>
          )}
          {isCompleted && task.result && (
            <span className="ml-auto text-xs text-muted-foreground">
              {t.message.completedChanges}
            </span>
          )}
        </div>
      </div>
    </div>
  );
});

/**
 * 紧凑版角色身份卡 — 复用 HUD AgentCreationCard 的视觉语言。
 * 在消息流中子 agent 创建时展示工牌式身份信息。
 */
function AgentIdentityCard({
  task,
  onClose,
}: {
  task: Subtask;
  onClose?: () => void;
}) {
  const { t } = useI18n();
  const displayName = task.name ?? task.description;
  const roleName = friendlyRoleName(task.role ?? task.subagent_type);
  // Motto is a short blurb; the full prompt appears only once, in the
  // scrollable brief section below.
  const motto =
    task.description && task.description !== displayName
      ? task.description
      : t.agentWorkbenchPages.defaultMotto;
  const brief = task.prompt ?? "";

  return (
    <div className="overflow-hidden rounded-lg border border-border-default bg-background shadow-[var(--shadow-xs)]">
      {/* 黑色标题条 — 复用 HUD 风格 */}
      <div className="flex items-center gap-2 rounded-md bg-foreground px-3 py-1.5 text-sm font-semibold text-background">
        <span className="truncate flex-1">{displayName}</span>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="flex size-5 shrink-0 items-center justify-center rounded text-background/70 transition-colors hover:bg-background/15 hover:text-background"
            aria-label={t.agentWorkbenchPages.collapse}
          >
            <ChevronDownIcon className="size-3.5" />
          </button>
        )}
      </div>
      <div className="flex items-center gap-3 px-3 py-3">
        {/* 大头像 */}
        <span
          className="flex size-12 shrink-0 items-center justify-center rounded-lg border border-border-default bg-muted/25 text-2xl"
          style={
            task.hue != null
              ? { background: `hsl(${task.hue} 70% 92%)` }
              : undefined
          }
        >
          {task.avatarEmoji ?? "🤖"}
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-base font-semibold text-foreground">
            {roleName}
          </div>
          <p className="mt-0.5 line-clamp-2 text-xs leading-5 text-muted-foreground">
            {motto}
          </p>
        </div>
      </div>
      {/* 角色说明 */}
      {brief && (
        <div className="border-t border-border-subtle px-3 py-2">
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground/60">
            {t.agentWorkbenchPages.roleDescription}
          </div>
          <div className="max-h-32 overflow-y-auto whitespace-pre-wrap break-words text-xs leading-5 text-foreground/80">
            {brief}
          </div>
        </div>
      )}
      {/* 底部标识 */}
      <div className="flex items-center gap-2 border-t border-border-subtle px-3 py-1.5">
        <span className="text-sm font-bold tracking-tight text-foreground/50">
          ECHO
        </span>
        {task.skills && task.skills.length > 0 && (
          <div className="ml-auto flex flex-wrap gap-1">
            {task.skills.slice(0, 3).map((skill) => (
              <span
                key={skill}
                className="rounded bg-muted/60 px-1.5 py-0.5 text-xs text-muted-foreground"
              >
                {skill}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

const MAX_VISIBLE_TASKS = 4;
// Without the compact prop (no production caller passes it) the grid still
// collapses on its own for large fan-outs, so the message stream doesn't
// turn into a wall of cards.
const AUTO_COLLAPSE_THRESHOLD = 8;
const AUTO_VISIBLE_TASKS = 6;
// In compact mode, allow more visible tasks since they take less space
const COMPACT_VISIBLE_TASKS = 12;

export function ParallelSubtasksGrid({
  taskIds,
  isLoading,
  onTaskClick,
  compact = false,
  forceCollapsed,
}: {
  taskIds: string[];
  isLoading: boolean;
  onTaskClick?: (taskId: string) => void;
  compact?: boolean;
  forceCollapsed?: boolean;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const { tasks } = useSubtaskContext();

  const selectedTask = selectedTaskId ? tasks[selectedTaskId] : null;

  if (taskIds.length === 0) return null;

  const isGrid = taskIds.length > 1;
  const shouldCollapse = compact
    ? taskIds.length > MAX_VISIBLE_TASKS
    : taskIds.length > AUTO_COLLAPSE_THRESHOLD;

  // Use forceCollapsed if provided, otherwise use local expanded state
  const effectivelyExpanded = forceCollapsed === true ? false : forceCollapsed === false ? true : expanded;
  const visibleLimit = compact ? COMPACT_VISIBLE_TASKS : (compact === false ? MAX_VISIBLE_TASKS : AUTO_VISIBLE_TASKS);
  const visibleIds =
    shouldCollapse && !effectivelyExpanded ? taskIds.slice(0, visibleLimit) : taskIds;
  const hiddenCount = taskIds.length - visibleIds.length;

  // 整体进度统计
  const stats = taskIds.reduce(
    (acc, id) => {
      const task = tasks[id];
      acc.total += 1;
      if (!task) {
        // Not yet registered in SubtaskContext — still part of the batch,
        // so count it as pending instead of shrinking the denominator.
        acc.pending += 1;
        return acc;
      }
      if (task.status === "completed") acc.done += 1;
      else if (task.status === "failed" || task.status === "timed_out" || task.status === "cancelled")
        acc.failed += 1;
      else if (isSubtaskActive(task.status)) acc.running += 1;
      else if (task.status === "pending") acc.pending += 1;
      return acc;
    },
    { done: 0, running: 0, pending: 0, failed: 0, total: 0 },
  );
  const overallPercent =
    stats.total > 0
      ? Math.round((stats.done / stats.total) * 100)
      : 0;
  const showSummary =
    isGrid && (stats.running > 0 || stats.done > 0 || stats.failed > 0);

  const renderTask = (taskId: string) => (
    <MiniSubtaskRow
      key={taskId}
      taskId={taskId}
      isLoading={isLoading}
      onClick={onTaskClick ? () => onTaskClick(taskId) : undefined}
      onDetailsClick={() => setSelectedTaskId(taskId)}
      compact={compact}
    />
  );

  return (
    <>
      <div className="space-y-2">
      {showSummary && (
        <div className="flex items-center gap-2 px-1 text-xs text-muted-foreground">
          <div className="h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-muted/60">
            <div
              className="h-full rounded-full bg-success transition-all duration-slow dark:bg-success"
              style={{ width: `${overallPercent}%` }}
            />
          </div>
          <span className="shrink-0 font-mono tabular-nums">
            {stats.done}/{stats.total}
          </span>
          {stats.running > 0 && (
            <span className="shrink-0 text-success">
              · {t.agentWorkbenchPages.subagentsRunning(stats.running)}
            </span>
          )}
          {stats.failed > 0 && (
            <span className="shrink-0 text-destructive">
              · {t.agentWorkbenchPages.subagentsFailed(stats.failed)}
            </span>
          )}
        </div>
      )}
      {isGrid && !compact ? (
        <div
          className={cn(
            "grid gap-2",
            visibleIds.length === 2
              ? "grid-cols-2"
              : visibleIds.length === 3
                ? "grid-cols-3"
                : "grid-cols-2",
          )}
        >
          {visibleIds.map(renderTask)}
        </div>
      ) : (
        <div className="space-y-1.5">
          {visibleIds.map(renderTask)}
        </div>
      )}
      {shouldCollapse && forceCollapsed === undefined && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex w-full items-center justify-center gap-1 rounded-md border border-dashed border-border-default py-1.5 text-xs text-muted-foreground transition-colors hover:border-border hover:bg-muted/35 hover:text-foreground"
        >
          {effectivelyExpanded ? (
            <>
              <ChevronUpIcon className="size-3" />
              <span>{t.message.collapseAgents}</span>
            </>
          ) : (
            <>
              <ChevronDownIcon className="size-3" />
              <span>{t.message.showMoreAgents(hiddenCount)}</span>
            </>
          )}
        </button>
      )}
    </div>

    {/* 详情侧边栏 */}
    <SubagentDetailsPanel
      task={selectedTask ?? null}
      open={selectedTaskId !== null}
      onOpenChange={(open) => {
        if (!open) setSelectedTaskId(null);
      }}
    />
  </>
  );
}
