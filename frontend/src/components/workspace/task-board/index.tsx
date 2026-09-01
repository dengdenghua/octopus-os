/**
 * TaskBoard -- unified kanban/timeline/list dashboard for all task types.
 *
 * Aggregates background tasks, quest tasks, and scheduled tasks into a
 * single visual dashboard with three view modes:
 *   A. Kanban View (default) -- 4 columns: Queued | Running | Completed | Failed
 *   B. Timeline View -- horizontal Gantt chart
 *   C. List View -- sortable table
 */

import {
  AlertCircleIcon,
  ArrowDownIcon,
  ArrowUpDownIcon,
  ArrowUpIcon,
  CalendarClockIcon,
  CheckCircle2Icon,
  ClockIcon,
  GanttChartIcon,
  KanbanIcon,
  LayoutListIcon,
  Loader2Icon,
  RefreshCwIcon,
} from "lucide-react";
import { lazy, Suspense } from "react";
import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  useTaskBoardStats,
  useTaskBoardTasks,
  useTaskBoardTimeline,
} from "@/core/task-board/hooks";
import type { KanbanColumnId, UnifiedTask } from "@/core/task-board/types";
import { KANBAN_COLUMNS, statusToColumn } from "@/core/task-board/types";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { StatsBar } from "./stats-bar";
import {
  formatDurationMs,
  formatRelativeTime,
  STATUS_STYLE,
  TaskCard,
  TYPE_COLORS,
  TYPE_ICONS,
} from "./task-card";
import { TimelineView } from "./timeline-view";

// ---------------------------------------------------------------------------
// View mode
// ---------------------------------------------------------------------------

type ViewMode = "kanban" | "timeline" | "list" | "schedules";

const CronSettingsPage = lazy(() =>
  import("@/components/workspace/settings/cron-settings-page").then((m) => ({
    default: m.CronSettingsPage,
  })),
);

const VIEW_MODE_ICONS: Record<ViewMode, React.ReactNode> = {
  kanban: <KanbanIcon className="size-3.5" />,
  timeline: <GanttChartIcon className="size-3.5" />,
  list: <LayoutListIcon className="size-3.5" />,
  schedules: <CalendarClockIcon className="size-3.5" />,
};

const VIEW_MODES: ViewMode[] = ["kanban", "timeline", "list", "schedules"];

// ---------------------------------------------------------------------------
// Kanban column config
// ---------------------------------------------------------------------------

const COLUMN_CONFIG: Record<
  KanbanColumnId,
  { color: string; bgColor: string; icon: React.ReactNode }
> = {
  queued: {
    color: "text-muted-foreground",
    bgColor: "bg-muted-foreground/5 border-muted-foreground/20",
    icon: <ClockIcon className="size-3.5" />,
  },
  running: {
    color: "text-warning",
    bgColor: "bg-warning/5 border-warning/20",
    icon: <Loader2Icon className="size-3.5 animate-spin" />,
  },
  completed: {
    color: "text-success",
    bgColor: "bg-success/5 border-success/20",
    icon: <CheckCircle2Icon className="size-3.5" />,
  },
  failed: {
    color: "text-destructive",
    bgColor: "bg-destructive/5 border-destructive/20",
    icon: <AlertCircleIcon className="size-3.5" />,
  },
};

// ---------------------------------------------------------------------------
// Kanban Column
// ---------------------------------------------------------------------------

function KanbanColumn({
  columnId,
  tasks,
}: {
  columnId: KanbanColumnId;
  tasks: UnifiedTask[];
}) {
  const { t } = useI18n();
  const cfg = COLUMN_CONFIG[columnId];
  const columnLabels: Record<KanbanColumnId, string> = {
    queued: t.taskBoard.queued,
    running: t.taskBoard.running,
    completed: t.taskBoard.completed,
    failed: t.taskBoard.failed,
  };
  return (
    <div
      className={cn(
        "flex min-w-[260px] max-w-[320px] flex-1 flex-col rounded-lg border",
        cfg.bgColor,
      )}
    >
      {/* Column header */}
      <div className="flex items-center gap-2 px-3 py-2.5 border-b border-inherit">
        <span
          className={cn(
            "flex items-center gap-1.5 text-sm font-semibold",
            cfg.color,
          )}
        >
          {cfg.icon}
          {columnLabels[columnId]}
        </span>
        <Badge
          variant="secondary"
          className="ml-auto text-xs px-1.5 py-0 h-5 min-w-[20px] justify-center"
        >
          {tasks.length}
        </Badge>
      </div>

      {/* Cards */}
      <ScrollArea className="flex-1 max-h-[calc(100vh-340px)]">
        <div className="space-y-2 p-2">
          {tasks.length === 0 ? (
            <div className="flex items-center justify-center py-8 text-xs text-muted-foreground/50">
              {t.taskBoard.noTasks}
            </div>
          ) : (
            tasks.map((task) => <TaskCard key={task.id} task={task} />)
          )}
        </div>
      </ScrollArea>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Kanban View
// ---------------------------------------------------------------------------

function KanbanView({ tasks }: { tasks: UnifiedTask[] }) {
  const columns = useMemo(() => {
    const grouped: Record<KanbanColumnId, UnifiedTask[]> = {
      queued: [],
      running: [],
      completed: [],
      failed: [],
    };
    for (const task of tasks) {
      const col = statusToColumn(task.status);
      grouped[col].push(task);
    }
    // Sort each column by updated_at descending
    for (const col of KANBAN_COLUMNS) {
      grouped[col].sort(
        (a, b) =>
          new Date(b.updated_at || b.created_at).getTime() -
          new Date(a.updated_at || a.created_at).getTime(),
      );
    }
    return grouped;
  }, [tasks]);

  return (
    <div className="flex gap-3 overflow-x-auto pb-2">
      {KANBAN_COLUMNS.map((colId) => (
        <KanbanColumn key={colId} columnId={colId} tasks={columns[colId]} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// List View
// ---------------------------------------------------------------------------

type SortKey = "type" | "name" | "status" | "duration_ms" | "created_at";
type SortDir = "asc" | "desc";

function SortIcon({ active, dir }: { active: boolean; dir: SortDir }) {
  if (!active)
    return <ArrowUpDownIcon className="size-3 text-muted-foreground/40" />;
  return dir === "asc" ? (
    <ArrowUpIcon className="size-3" />
  ) : (
    <ArrowDownIcon className="size-3" />
  );
}

function ListView({ tasks }: { tasks: UnifiedTask[] }) {
  const { t } = useI18n();
  const TYPE_LABELS: Record<string, string> = {
    background: t.taskBoard.background,
    quest: t.taskBoard.quest,
    scheduled: t.taskBoard.scheduled,
    intelligence: t.taskBoard.intelligence,
  };
  const STATUS_LABELS: Record<string, string> = {
    queued: t.taskBoard.queued,
    running: t.taskBoard.running,
    completed: t.taskBoard.completed,
    failed: t.taskBoard.failed,
    paused: t.taskBoard.paused,
    cancelled: t.taskBoard.cancelled,
  };
  const [sortKey, setSortKey] = useState<SortKey>("created_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const sorted = useMemo(() => {
    const copy = [...tasks];
    copy.sort((a, b) => {
      let cmp = 0;
      switch (sortKey) {
        case "type":
          cmp = a.type.localeCompare(b.type);
          break;
        case "name":
          cmp = a.name.localeCompare(b.name);
          break;
        case "status":
          cmp = a.status.localeCompare(b.status);
          break;
        case "duration_ms":
          cmp = a.duration_ms - b.duration_ms;
          break;
        case "created_at":
          cmp =
            new Date(a.created_at || 0).getTime() -
            new Date(b.created_at || 0).getTime();
          break;
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
    return copy;
  }, [tasks, sortKey, sortDir]);

  const columns: { key: SortKey; label: string; className?: string }[] = [
    { key: "type", label: t.taskBoard.type, className: "w-[100px]" },
    { key: "name", label: t.taskBoard.name, className: undefined },
    { key: "status", label: t.taskBoard.status, className: "w-[120px]" },
    { key: "duration_ms", label: t.taskBoard.duration, className: "w-[100px]" },
    { key: "created_at", label: t.taskBoard.created, className: "w-[120px]" },
  ];

  return (
    <div className="overflow-x-auto max-w-full rounded-lg border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b bg-muted/30">
            {columns.map((col) => (
              <th
                key={col.key}
                aria-sort={
                  sortKey === col.key
                    ? sortDir === "asc"
                      ? "ascending"
                      : "descending"
                    : "none"
                }
                className={cn(
                  "px-1 py-1 text-left font-medium text-muted-foreground",
                  col.className,
                )}
              >
                <button
                  type="button"
                  className="flex min-h-8 w-full select-none items-center gap-1 rounded-md px-2 text-left transition-colors hover:bg-accent/50 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => handleSort(col.key)}
                >
                  {col.label}
                  <SortIcon active={sortKey === col.key} dir={sortDir} />
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 ? (
            <tr>
              <td
                colSpan={5}
                className="px-3 py-8 text-center text-muted-foreground"
              >
                {t.taskBoard.noTasks}
              </td>
            </tr>
          ) : (
            sorted.map((task) => {
              const statusCfg =
                STATUS_STYLE[task.status] ?? STATUS_STYLE.queued;
              return (
                <tr
                  key={task.id}
                  className="border-b last:border-0 hover:bg-accent/30 transition-colors"
                >
                  {/* Type */}
                  <td className="px-3 py-2.5">
                    <div
                      className={cn(
                        "flex items-center gap-1.5",
                        TYPE_COLORS[task.type],
                      )}
                    >
                      {TYPE_ICONS[task.type]}
                      <span className="text-xs font-medium">
                        {TYPE_LABELS[task.type]}
                      </span>
                    </div>
                  </td>

                  {/* Name */}
                  <td className="px-3 py-2.5">
                    <div className="min-w-0">
                      <p className="truncate font-medium">
                        {task.name || task.id}
                      </p>
                      {task.phase && (
                        <p className="truncate text-xs text-muted-foreground">
                          {task.phase}
                        </p>
                      )}
                    </div>
                  </td>

                  {/* Status */}
                  <td className="px-3 py-2.5">
                    <span
                      className={cn(
                        "inline-flex items-center gap-1 rounded-lg border px-1.5 py-0.5 text-xs font-medium leading-none",
                        statusCfg.badgeClass,
                      )}
                    >
                      {statusCfg.icon}
                      {STATUS_LABELS[task.status] ?? task.status}
                    </span>
                  </td>

                  {/* Duration */}
                  <td className="px-3 py-2.5 tabular-nums text-muted-foreground">
                    {formatDurationMs(task.duration_ms)}
                  </td>

                  {/* Created */}
                  <td className="px-3 py-2.5 text-muted-foreground">
                    {formatRelativeTime(task.created_at, {
                      justNow: t.taskBoard.justNow,
                      minutesAgo: t.taskBoard.minutesAgo,
                      hoursAgo: t.taskBoard.hoursAgo,
                      daysAgo: t.taskBoard.daysAgo,
                    })}
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Type filter pills
// ---------------------------------------------------------------------------

type TypeFilter = "all" | "background" | "quest" | "scheduled" | "intelligence";

type TaskBoardProps = {
  initialViewMode?: ViewMode;
  initialTypeFilter?: TypeFilter;
  compact?: boolean;
};

function TypeFilterPills({
  value,
  onChange,
  tasks,
}: {
  value: TypeFilter;
  onChange: (v: TypeFilter) => void;
  tasks: UnifiedTask[];
}) {
  const { t: i18n } = useI18n();
  const counts = useMemo(() => {
    const c: Record<string, number> = {
      all: tasks.length,
      background: 0,
      quest: 0,
      scheduled: 0,
      intelligence: 0,
    };
    for (const t of tasks) c[t.type] = (c[t.type] ?? 0) + 1;
    return c;
  }, [tasks]);

  const options: { key: TypeFilter; label: string }[] = [
    { key: "all", label: i18n.taskBoard.all },
    { key: "background", label: i18n.taskBoard.background },
    { key: "quest", label: i18n.taskBoard.quest },
    { key: "scheduled", label: i18n.taskBoard.scheduled },
    { key: "intelligence", label: i18n.taskBoard.intelligence },
  ];

  return (
    <div
      className="flex max-w-full items-center gap-1 overflow-x-auto pb-1"
      role="group"
      aria-label={i18n.taskBoard.filterByType}
    >
      {options.map((opt) => (
        <Button
          key={opt.key}
          variant={value === opt.key ? "secondary" : "ghost"}
          size="sm"
          className={cn(
            "h-7 px-2.5 text-xs",
            value === opt.key && "font-semibold",
          )}
          onClick={() => onChange(opt.key)}
          aria-pressed={value === opt.key}
        >
          {opt.label}
          {(counts[opt.key] ?? 0) > 0 && (
            <span
              aria-hidden="true"
              className="ml-1 text-xs text-muted-foreground"
            >
              {counts[opt.key]}
            </span>
          )}
        </Button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main TaskBoard
// ---------------------------------------------------------------------------

export function TaskBoard({
  initialViewMode = "kanban",
  initialTypeFilter,
  compact = false,
}: TaskBoardProps = {}) {
  const { t } = useI18n();
  const [searchParams] = useSearchParams();
  const initialType = searchParams.get("type");
  const [viewMode, setViewMode] = useState<ViewMode>(initialViewMode);
  const [typeFilter, setTypeFilter] = useState<TypeFilter>(
    initialType === "background" ||
      initialType === "quest" ||
      initialType === "scheduled" ||
      initialType === "intelligence"
      ? initialType
      : initialTypeFilter
        ? initialTypeFilter
        : "all",
  );

  // Fetch data
  const {
    data: taskData,
    loading: tasksLoading,
    error: tasksError,
    refresh: refreshTasks,
  } = useTaskBoardTasks({
    type: typeFilter === "all" ? undefined : typeFilter,
  });
  const { stats, loading: statsLoading } = useTaskBoardStats();
  const { data: timelineData, loading: timelineLoading } = useTaskBoardTimeline(
    {
      type: typeFilter === "all" ? undefined : typeFilter,
    },
  );

  const tasks = taskData.tasks;

  const handleRefresh = useCallback(() => {
    refreshTasks();
  }, [refreshTasks]);

  return (
    <div
      className={cn(
        "ui-density-stack flex h-full flex-col",
        compact ? "p-0" : "ui-density-page",
      )}
    >
      {/* Page header */}
      <div className="ui-density-panel flex flex-col items-stretch justify-between gap-3 rounded-lg border border-border-default bg-card/60 sm:flex-row sm:items-center">
        <div className="min-w-0">
          <h1 className="text-xl font-bold tracking-tight">
            {t.taskBoard.title}
          </h1>
          <p className="text-sm text-muted-foreground">
            {t.taskBoard.description}
          </p>
        </div>

        <div className="flex items-center gap-2 sm:shrink-0">
          <Button
            variant="outline"
            size="sm"
            className="h-8 w-full sm:w-auto"
            onClick={handleRefresh}
            disabled={tasksLoading}
          >
            <RefreshCwIcon className="size-3.5 mr-1.5" />
            {t.taskBoard.refresh}
          </Button>
        </div>
      </div>

      {/* Stats bar */}
      <StatsBar stats={stats} loading={statsLoading} />

      {/* Toolbar: view mode + filters */}
      <div className="ui-density-panel flex flex-col items-stretch justify-between gap-3 rounded-lg border border-border-subtle bg-card/40 xl:flex-row xl:items-center">
        <TypeFilterPills
          value={typeFilter}
          onChange={setTypeFilter}
          tasks={taskData.tasks}
        />

        <Tabs
          value={viewMode}
          onValueChange={(v) => setViewMode(v as ViewMode)}
          className="max-w-full overflow-x-auto pb-1"
        >
          <TabsList className="h-8 w-max min-w-full sm:min-w-0">
            {VIEW_MODES.map((vm) => (
              <TabsTrigger
                key={vm}
                value={vm}
                className="gap-1.5 text-xs px-3 h-7"
              >
                {VIEW_MODE_ICONS[vm]}
                {t.taskBoard[vm]}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      {/* Error state */}
      {tasksError && viewMode !== "schedules" && (
        <div
          role="alert"
          className="ui-density-panel flex flex-col items-start justify-between gap-3 rounded-lg border border-destructive/20 bg-destructive/5 text-sm text-destructive sm:flex-row sm:items-center"
        >
          <span className="flex items-center gap-2">
            <AlertCircleIcon className="size-4 shrink-0" />
            {t.taskBoard.loadFailed}
          </span>
          <Button variant="outline" size="sm" onClick={handleRefresh}>
            <RefreshCwIcon className="mr-1.5 size-3.5" />
            {t.taskBoard.retry}
          </Button>
        </div>
      )}

      {/* Loading state */}
      {tasksLoading && tasks.length === 0 && viewMode !== "schedules" && (
        <div
          role="status"
          className="flex items-center justify-center rounded-lg border border-border-subtle bg-card/40 py-20"
        >
          <Loader2Icon className="size-6 animate-spin text-muted-foreground" />
          <span className="sr-only">{t.common.loading}</span>
        </div>
      )}

      {/* Empty state */}
      {!tasksLoading &&
        tasks.length === 0 &&
        !tasksError &&
        viewMode !== "schedules" && (
          <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border-default bg-card/30 py-20 text-muted-foreground">
            <KanbanIcon className="size-12 mb-3 opacity-30" />
            <p className="text-lg font-medium">{t.taskBoard.noTasks}</p>
            <p className="text-sm">{t.taskBoard.noTasksDescription}</p>
          </div>
        )}

      {/* View content */}
      {viewMode === "schedules" ? (
        <div className="flex-1 min-h-0 overflow-auto">
          <Suspense
            fallback={
              <div className="flex items-center justify-center py-20">
                <Loader2Icon className="size-6 animate-spin text-muted-foreground" />
                <span className="sr-only">{t.common.loading}</span>
              </div>
            }
          >
            <CronSettingsPage />
          </Suspense>
        </div>
      ) : tasks.length > 0 ? (
        <div className="flex-1 min-h-0">
          {viewMode === "kanban" && <KanbanView tasks={tasks} />}
          {viewMode === "timeline" && (
            <TimelineView data={timelineData} loading={timelineLoading} />
          )}
          {viewMode === "list" && <ListView tasks={tasks} />}
        </div>
      ) : null}
    </div>
  );
}

export default TaskBoard;
