/**
 * Parallel Agents Panel
 *
 * Visualizes up to 100 concurrent SubAgent executions:
 * - Grid/list view of all running agents
 * - Each agent card shows: task description, status, progress, model
 * - Real-time progress via SSE
 * - Cancel individual or all agents
 * - Results aggregation view
 * - Simple dependency DAG visualization
 */

import {
  ActivityIcon,
  AlertTriangleIcon,
  BanIcon,
  BotIcon,
  CheckCircle2Icon,
  ChevronLeftIcon,
  ClockIcon,
  CpuIcon,
  GridIcon,
  LayoutListIcon,
  ListChecksIcon,
  Loader2Icon,
  MaximizeIcon,
  MinimizeIcon,
  PauseCircleIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  SearchIcon,
  ShieldCheckIcon,
  SquareIcon,
  TimerIcon,
  XCircleIcon,
  XIcon,
  ZapIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import {
  cancelAll as apiCancelAll,
  cancelTask as apiCancelTask,
  fetchBatch as apiFetchBatch,
  fetchBatchRecoverySnapshot as apiFetchBatchRecoverySnapshot,
  fetchOrchestratorStatus as apiFetchOrchestratorStatus,
  STATUS_BG,
  STATUS_TEXT_COLOR as STATUS_COLORS,
  type BatchRecoverySnapshot,
  type BatchResult,
  type OrchestratorStatus,
  type ParallelBatchCoordinationSummary,
  type TaskResult,
} from "@/core/parallel-agents/api";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// UI-only types
// ---------------------------------------------------------------------------

type ViewMode = "grid" | "list";

// ---------------------------------------------------------------------------
// Status helpers
// ---------------------------------------------------------------------------

function getStatusIcon(status: string, className?: string) {
  const cls = cn("size-3.5", STATUS_COLORS[status], className);
  switch (status) {
    case "running":
      return <Loader2Icon className={cn(cls, "animate-spin")} />;
    case "completed":
      return <CheckCircle2Icon className={cls} />;
    case "failed":
      return <XCircleIcon className={cls} />;
    case "cancelled":
      return <BanIcon className={cls} />;
    case "timed_out":
      return <ClockIcon className={cls} />;
    case "pending":
      return <PauseCircleIcon className={cls} />;
    default:
      return <CpuIcon className={cls} />;
  }
}

function batchStatusDotClass(status: string): string {
  switch (status) {
    case "running":
      return "bg-info";
    case "completed":
      return "bg-success";
    case "failed":
    case "timed_out":
      return "bg-destructive";
    case "cancelled":
      return "bg-warning";
    case "partial":
      return "bg-warning";
    default:
      return "bg-muted-foreground";
  }
}

function pickFocusBatchId(batches: Record<string, string>): string | null {
  const entries = Object.entries(batches);
  const running = entries.find(([, status]) => status === "running");
  return (running ?? entries[0])?.[0] ?? null;
}

function parallelStatusLabel(status: string, labels: Record<string, string>) {
  return (
    labels[status] ??
    labels[status.replace(/_([a-z])/g, (_, c) => c.toUpperCase())] ??
    status
  );
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "--";
  if (seconds < 1) return `${(seconds * 1000).toFixed(0)}ms`;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${(seconds / 60).toFixed(1)}m`;
}

function hasCoordinationSignal(
  summary: ParallelBatchCoordinationSummary | undefined,
): summary is ParallelBatchCoordinationSummary {
  if (!summary) return false;
  return Boolean(
    summary.recommended_next_action ||
      summary.primary_task_id ||
      summary.failed_task_ids?.length ||
      summary.cancelled_task_ids?.length ||
      summary.dependency_blocked_task_ids?.length ||
      summary.conflict_count ||
      summary.contract_issue_count ||
      summary.contract_warning_count,
  );
}

function formatCoordinationAction(action: string): string {
  if (!action) return "";
  return action
    .replace(/^use_/, "use ")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function CoordinationSummaryNotice({
  summary,
}: {
  summary: ParallelBatchCoordinationSummary | undefined;
}) {
  const { t } = useI18n();
  if (!hasCoordinationSignal(summary)) return null;

  const labels = t.parallelAgents;
  const checkpoint = summary.checkpoint?.after_sequence;
  const failedTaskIds = summary.failed_task_ids ?? [];
  const cancelledTaskIds = summary.cancelled_task_ids ?? [];
  const dependencyBlockedTaskIds = summary.dependency_blocked_task_ids ?? [];
  const warningCount =
    (summary.conflict_count ?? 0) +
    (summary.contract_issue_count ?? 0) +
    (summary.contract_warning_count ?? 0);

  return (
    <div className="border-b bg-muted/20 px-4 py-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        <span className="inline-flex items-center gap-1 font-medium text-foreground">
          <ListChecksIcon className="size-3.5 text-muted-foreground" />
          {labels.coordinationSummary}
        </span>
        <span
          className={cn(
            "font-medium",
            summary.ready
              ? "text-success"
              : "text-warning",
          )}
        >
          {labels.coordinationAction(
            formatCoordinationAction(summary.recommended_next_action),
          )}
        </span>
        {summary.primary_task_id && (
          <span className="text-muted-foreground font-mono">
            {labels.primaryTask(summary.primary_task_id)}
          </span>
        )}
        {failedTaskIds.length > 0 && (
          <span className="text-destructive">
            {labels.failedTasks(failedTaskIds.length)}
          </span>
        )}
        {cancelledTaskIds.length > 0 && (
          <span className="text-warning">
            {labels.cancelledTasks(cancelledTaskIds.length)}
          </span>
        )}
        {dependencyBlockedTaskIds.length > 0 && (
          <span className="text-warning">
            {labels.dependencyBlocked(dependencyBlockedTaskIds.length)}
          </span>
        )}
        {warningCount > 0 && (
          <span className="inline-flex items-center gap-1 text-warning">
            <AlertTriangleIcon className="size-3" />
            {labels.coordinationWarnings(warningCount)}
          </span>
        )}
        {typeof checkpoint === "number" && (
          <span className="text-muted-foreground font-mono">
            {labels.checkpointSequence(checkpoint)}
          </span>
        )}
      </div>
    </div>
  );
}

function RecoverySnapshotNotice({
  snapshot,
}: {
  snapshot: BatchRecoverySnapshot | null;
}) {
  const { t } = useI18n();
  if (!snapshot) return null;

  const labels = t.parallelAgents;
  const rerunnable = snapshot.recovery_hints.rerunnable_task_ids ?? [];
  const failed = snapshot.recovery_hints.failed_task_ids ?? [];
  const blocked = snapshot.recovery_hints.blocked_by_dependency ?? [];
  const afterSequence = snapshot.recovery_hints.checkpoint?.after_sequence;
  const rawOutputsIncluded =
    snapshot.safety.raw_subagent_outputs_included === true ||
    snapshot.safety.event_payloads_included === true ||
    snapshot.safety.owner_id_included === true;

  if (
    !snapshot.resume_available &&
    failed.length === 0 &&
    blocked.length === 0
  ) {
    return null;
  }

  return (
    <div className="border-b bg-warning/5 px-4 py-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        <span className="inline-flex items-center gap-1 font-medium text-warning">
          <RotateCcwIcon className="size-3.5" />
          {labels.recoveryReady}
        </span>
        {rerunnable.length > 0 && (
          <span className="text-muted-foreground">
            {labels.rerunnableTasks(rerunnable.length)}
          </span>
        )}
        {failed.length > 0 && (
          <span className="text-destructive">
            {labels.failedTasks(failed.length)}
          </span>
        )}
        {blocked.length > 0 && (
          <span className="text-warning">
            {labels.dependencyBlocked(blocked.length)}
          </span>
        )}
        {typeof afterSequence === "number" && (
          <span className="text-muted-foreground font-mono">
            {labels.checkpointSequence(afterSequence)}
          </span>
        )}
        <span
          className={cn(
            "inline-flex items-center gap-1",
            rawOutputsIncluded
              ? "text-destructive"
              : "text-success",
          )}
        >
          <ShieldCheckIcon className="size-3" />
          {rawOutputsIncluded ? labels.recoveryUnsafe : labels.recoverySafe}
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Animated progress ring
// ---------------------------------------------------------------------------

function ProgressRing({
  completed,
  total,
  failed,
  size = 48,
}: {
  completed: number;
  total: number;
  failed: number;
  size?: number;
}) {
  const radius = (size - 6) / 2;
  const circumference = 2 * Math.PI * radius;
  const successPct = total > 0 ? completed / total : 0;
  const failPct = total > 0 ? failed / total : 0;

  return (
    <svg width={size} height={size} className="shrink-0">
      {/* Background ring */}
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="currentColor"
        strokeWidth={3}
        className="text-muted/30"
      />
      {/* Success arc */}
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="currentColor"
        strokeWidth={3}
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - successPct)}
        strokeLinecap="round"
        className="text-success transition-colors duration-slow"
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      {/* Failed arc */}
      {failPct > 0 && (
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={3}
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - failPct)}
          strokeLinecap="round"
          className="text-destructive transition-colors duration-slow"
          transform={`rotate(${-90 + successPct * 360} ${size / 2} ${size / 2})`}
        />
      )}
      {/* Center text */}
      <text
        x={size / 2}
        y={size / 2}
        textAnchor="middle"
        dominantBaseline="central"
        className="fill-foreground text-xs font-bold"
      >
        {completed}/{total}
      </text>
    </svg>
  );
}

// ---------------------------------------------------------------------------
// DAG Visualization (simple)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Agent Card
// ---------------------------------------------------------------------------

function AgentCard({
  task,
  index,
  compact,
  onCancel,
  onClick,
}: {
  task: TaskResult;
  index: number;
  compact?: boolean;
  onCancel?: (taskId: string) => void;
  onClick?: () => void;
}) {
  const { t } = useI18n();
  const isTerminal = ["completed", "failed", "cancelled", "timed_out"].includes(
    task.status,
  );

  if (compact) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-left transition-colors transition-shadow hover:shadow-[var(--shadow-xs)]",
          STATUS_BG[task.status] ?? "bg-card",
        )}
      >
        {getStatusIcon(task.status)}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1">
            <span className="truncate text-xs font-medium">
              {task.subagent_name || `Agent-${index}`}
            </span>
            <span className="text-muted-foreground font-mono text-xs">
              #{String(index).padStart(2, "0")}
            </span>
          </div>
          <p className="text-muted-foreground truncate text-xs">
            {task.result?.slice(0, 60) ?? task.error ?? "Pending..."}
          </p>
        </div>
        {task.duration_seconds !== null && (
          <span className="text-muted-foreground text-xs">
            {formatDuration(task.duration_seconds)}
          </span>
        )}
      </button>
    );
  }

  return (
    <div
      className={cn(
        "rounded-lg border transition-colors",
        STATUS_BG[task.status] ?? "bg-card",
      )}
    >
      <div className="flex items-start gap-3 p-3">
        {/* Avatar */}
        <div className="relative shrink-0">
          <div
            className={cn(
              "flex h-10 w-10 items-center justify-center rounded-lg",
              task.status === "running" ? "bg-info/15" : "bg-muted",
            )}
          >
            <BotIcon className="text-muted-foreground size-5" />
          </div>
          <span className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-lg bg-foreground text-micro font-bold text-background">
            {String(index).padStart(2, "0")}
          </span>
        </div>

        {/* Content */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold">
              {task.subagent_name || `Agent-${index}`}
            </span>
            {getStatusIcon(task.status)}
            <span
              className={cn(
                "text-xs font-medium",
                STATUS_COLORS[task.status],
              )}
            >
              {parallelStatusLabel(
                task.status,
                t.parallelAgents.statusLabels as Record<string, string>,
              )}
            </span>
            {task.duration_seconds !== null && (
              <span className="text-muted-foreground ml-auto flex items-center gap-0.5 text-xs">
                <TimerIcon className="size-2.5" />
                {formatDuration(task.duration_seconds)}
              </span>
            )}
          </div>

          {/* Task ID */}
          <p className="text-muted-foreground mt-0.5 font-mono text-xs">
            {task.task_id}
          </p>

          {/* Result or error preview */}
          {task.result && (
            <p className="mt-1.5 line-clamp-2 text-xs leading-relaxed text-foreground/80">
              {task.result.slice(0, 200)}
              {task.result.length > 200 ? "..." : ""}
            </p>
          )}
          {task.error && (
            <p className="mt-1.5 line-clamp-2 text-xs text-destructive">
              {task.error}
            </p>
          )}

          {/* Running indicator */}
          {task.status === "running" && (
            <div className="mt-2 flex items-center gap-[3px]">
              {Array.from({ length: 8 }).map((_, i) => (
                <span
                  key={i}
                  className="size-[5px] rounded-lg bg-info"
                  style={{
                    opacity: 0.25,
                    animation: `dotPulse 1.2s ease-in-out ${i * 0.12}s infinite`,
                  }}
                />
              ))}
            </div>
          )}
        </div>

        {/* Cancel button */}
        {!isTerminal && onCancel && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onCancel(task.task_id);
            }}
            className="text-muted-foreground hover:text-destructive shrink-0 rounded p-1 transition-colors"
          >
            <XIcon className="size-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Aggregated Results View
// ---------------------------------------------------------------------------

function AggregatedResultsView({
  batch,
  onBack,
}: {
  batch: BatchResult;
  onBack: () => void;
}) {
  const { t } = useI18n();
  const [showRaw, setShowRaw] = useState(false);

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex items-center gap-2 border-b px-4 py-3">
        <button
          type="button"
          onClick={onBack}
          aria-label={t.common.back}
          className="text-muted-foreground hover:text-foreground"
        >
          <ChevronLeftIcon className="size-4" />
        </button>
        <span className="text-sm font-semibold">
          {t.parallelAgents.aggregatedResults}
        </span>
        <span className="text-muted-foreground text-xs">
          {batch.aggregation_strategy}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={() => setShowRaw(!showRaw)}
            className={cn(
              "rounded px-2 py-0.5 text-xs font-medium transition-colors",
              showRaw
                ? "bg-foreground/10 text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {showRaw
              ? t.parallelAgents.aggregated
              : t.parallelAgents.rawResults}
          </button>
        </div>
      </div>

      {/* Conflicts banner */}
      {batch.conflicts.length > 0 && (
        <div className="border-b bg-warning/5 px-4 py-2">
          <div className="flex items-center gap-1.5 text-warning">
            <AlertTriangleIcon className="size-3.5" />
            <span className="text-xs font-medium">
              {t.parallelAgents.conflictsDetected(batch.conflicts.length)}
            </span>
          </div>
          {batch.conflicts.map((c, i) => (
            <p key={i} className="text-muted-foreground mt-1 text-xs">
              {c}
            </p>
          ))}
        </div>
      )}

      <CoordinationSummaryNotice summary={batch.coordination_summary} />

      <div className="flex-1 overflow-y-auto px-4 py-3">
        {showRaw ? (
          <div className="space-y-3">
            {batch.results.map((r, i) => (
              <div key={r.task_id} className="rounded-lg border p-3">
                <div className="mb-1.5 flex items-center gap-2">
                  <span className="text-xs font-semibold">
                    {r.subagent_name || `Agent-${i + 1}`}
                  </span>
                  {getStatusIcon(r.status)}
                  {r.duration_seconds !== null && (
                    <span className="text-muted-foreground text-xs">
                      {formatDuration(r.duration_seconds)}
                    </span>
                  )}
                </div>
                <p className="whitespace-pre-wrap text-xs leading-relaxed text-foreground/80">
                  {r.result || r.error || t.parallelAgents.noOutput}
                </p>
              </div>
            ))}
          </div>
        ) : (
          <div className="whitespace-pre-wrap text-xs leading-relaxed text-foreground/80">
            {batch.aggregated_content || t.parallelAgents.noOutput}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Panel
// ---------------------------------------------------------------------------

export function ParallelAgentsPanel({ className }: { className?: string }) {
  const { t } = useI18n();
  const [status, setStatus] = useState<OrchestratorStatus | null>(null);
  const [activeBatch, setActiveBatch] = useState<BatchResult | null>(null);
  const [recoverySnapshot, setRecoverySnapshot] =
    useState<BatchRecoverySnapshot | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("grid");
  const [collapsed, setCollapsed] = useState(false);
  const [showResults, setShowResults] = useState(false);
  const [filter, setFilter] = useState<string>("");
  const pollRef = useRef<ReturnType<typeof setInterval>>(undefined);

  // Fetch orchestrator status periodically
  const fetchStatus = useCallback(async () => {
    const data = await apiFetchOrchestratorStatus();
    if (data) setStatus(data);
  }, []);

  // Fetch batch details for active batches
  const fetchBatch = useCallback(async (batchId: string) => {
    const data = await apiFetchBatch(batchId);
    if (!data) return;
    setActiveBatch(data);
    if (data.status === "running") {
      setRecoverySnapshot(null);
      return;
    }
    const snapshot = await apiFetchBatchRecoverySnapshot(batchId);
    setRecoverySnapshot(snapshot);
  }, []);

  useEffect(() => {
    fetchStatus();
    pollRef.current = setInterval(fetchStatus, 3000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [fetchStatus]);

  // Auto-fetch the first running batch, or keep the latest terminal batch
  // visible so failed/cancelled evidence is not hidden behind an empty state.
  useEffect(() => {
    if (!status) return;
    const nextBatchId = pickFocusBatchId(status.batches);
    if (!nextBatchId) return;
    const nextBatchStatus = status.batches[nextBatchId];
    if (
      nextBatchStatus === "running" ||
      nextBatchId !== activeBatch?.batch_id ||
      nextBatchStatus !== activeBatch?.status
    ) {
      fetchBatch(nextBatchId);
    }
  }, [activeBatch?.batch_id, activeBatch?.status, status, fetchBatch]);

  // Cancel handlers
  const cancelTask = useCallback(
    async (taskId: string) => {
      await apiCancelTask(taskId);
      fetchStatus();
    },
    [fetchStatus],
  );

  const cancelAll = useCallback(async () => {
    await apiCancelAll();
    fetchStatus();
  }, [fetchStatus]);

  const totalActive =
    (status?.active_count ?? 0) + (status?.pending_count ?? 0);
  const isActive = totalActive > 0 || (activeBatch?.results?.length ?? 0) > 0;
  const batchCount = Object.keys(status?.batches ?? {}).length;

  const filteredResults = useMemo(() => {
    if (!activeBatch) return [];
    if (!filter) return activeBatch.results;
    const lower = filter.toLowerCase();
    return activeBatch.results.filter(
      (r) =>
        r.subagent_name.toLowerCase().includes(lower) ||
        r.task_id.toLowerCase().includes(lower) ||
        r.status.toLowerCase().includes(lower),
    );
  }, [activeBatch, filter]);

  return (
    <div className={cn("flex flex-col", className)}>
      {/* Header */}
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <ZapIcon className="size-4 text-warning" />
          {t.parallelAgents.title}
        </div>
        <div className="flex items-center gap-2">
          {isActive && (
            <span className="inline-flex items-center gap-1 rounded-lg bg-info/10 px-2 py-0.5 text-xs font-medium text-info">
              <span className="size-1.5 animate-pulse rounded-lg bg-info" />
              {totalActive} {t.parallelAgents.active}
            </span>
          )}
          {status && (
            <span className="text-muted-foreground text-xs">
              {t.parallelAgents.max}: {status.max_concurrency}
            </span>
          )}
          <button
            type="button"
            className="text-muted-foreground hover:text-foreground transition-colors"
            onClick={() => setCollapsed(!collapsed)}
          >
            {collapsed ? (
              <MaximizeIcon className="size-3.5" />
            ) : (
              <MinimizeIcon className="size-3.5" />
            )}
          </button>
        </div>
      </div>

      {collapsed ? (
        <div className="text-muted-foreground px-4 py-3 text-center text-xs">
          {isActive
            ? t.parallelAgents.agentsRunning(totalActive)
            : t.parallelAgents.noActiveTasks}
        </div>
      ) : showResults && activeBatch ? (
        <AggregatedResultsView
          batch={activeBatch}
          onBack={() => setShowResults(false)}
        />
      ) : !isActive && !activeBatch ? (
        /* Empty state */
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-4 py-12">
          <div className="rounded-lg bg-muted/40 p-4">
            <ZapIcon className="text-muted-foreground/40 size-8" />
          </div>
          <p className="text-muted-foreground text-sm font-medium">
            {t.parallelAgents.noParallelTasks}
          </p>
          <p className="text-muted-foreground/60 text-center text-xs leading-relaxed">
            {t.parallelAgents.noParallelTasksHint}
          </p>
        </div>
      ) : (
        <div className="flex flex-1 flex-col">
          {/* Status summary bar */}
          <div className="flex items-center gap-3 border-b px-4 py-2">
            {activeBatch && (
              <ProgressRing
                completed={activeBatch.completed_tasks}
                total={activeBatch.total_tasks}
                failed={activeBatch.failed_tasks + activeBatch.cancelled_tasks}
                size={36}
              />
            )}
            <div className="min-w-0 flex-1">
              {status && (
                <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
                  <span className="text-success">
                    {status.completed_count} {t.parallelAgents.completed}
                  </span>
                  {status.failed_count > 0 && (
                    <span className="text-destructive">
                      {status.failed_count} {t.parallelAgents.failed}
                    </span>
                  )}
                  {status.cancelled_count > 0 && (
                    <span className="text-warning">
                      {status.cancelled_count} {t.parallelAgents.cancelled}
                    </span>
                  )}
                  <span className="text-muted-foreground">
                    {batchCount} batch(es)
                  </span>
                </div>
              )}
            </div>

            {/* Actions */}
            <div className="flex items-center gap-1.5">
              {/* View mode toggle */}
              <button
                type="button"
                onClick={() =>
                  setViewMode(viewMode === "grid" ? "list" : "grid")
                }
                className="text-muted-foreground hover:text-foreground rounded p-1"
              >
                {viewMode === "grid" ? (
                  <LayoutListIcon className="size-3.5" />
                ) : (
                  <GridIcon className="size-3.5" />
                )}
              </button>

              {/* Show results */}
              {activeBatch &&
                activeBatch.status !== "running" &&
                activeBatch.aggregated_content && (
                  <button
                    type="button"
                    onClick={() => setShowResults(true)}
                    className="text-muted-foreground hover:text-foreground rounded p-1"
                  >
                    <ActivityIcon className="size-3.5" />
                  </button>
                )}

              {/* Refresh */}
              <button
                type="button"
                onClick={() => {
                  fetchStatus();
                  if (activeBatch) fetchBatch(activeBatch.batch_id);
                }}
                className="text-muted-foreground hover:text-foreground rounded p-1"
              >
                <RefreshCwIcon className="size-3.5" />
              </button>

              {/* Cancel all */}
              {totalActive > 0 && (
                <button
                  type="button"
                  onClick={cancelAll}
                  className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                >
                  <SquareIcon className="size-3.5" />
                </button>
              )}
            </div>
          </div>

          <RecoverySnapshotNotice snapshot={recoverySnapshot} />
          <CoordinationSummaryNotice
            summary={
              recoverySnapshot?.coordination_summary ??
              activeBatch?.coordination_summary
            }
          />

          {/* Filter bar */}
          {activeBatch && activeBatch.results.length > 5 && (
            <div className="border-b px-4 py-1.5">
              <div className="flex items-center gap-1.5 rounded-lg bg-muted/50 px-2 py-1">
                <SearchIcon className="text-muted-foreground size-3" />
                <input
                  type="text"
                  placeholder={t.parallelAgents.filterAgents}
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  className="flex-1 bg-transparent text-xs outline-none placeholder:text-muted-foreground/50"
                />
              </div>
            </div>
          )}

          {/* Task cards */}
          <div
            className={cn(
              "flex-1 overflow-y-auto p-3",
              viewMode === "grid"
                ? "grid auto-rows-min grid-cols-1 gap-2 sm:grid-cols-2"
                : "space-y-1.5",
            )}
          >
            {filteredResults.map((task, idx) => (
              <AgentCard
                key={task.task_id}
                task={task}
                index={idx + 1}
                compact={viewMode === "list"}
                onCancel={
                  task.status === "running" || task.status === "pending"
                    ? cancelTask
                    : undefined
                }
                onClick={() => {
                  /* could open detail view */
                }}
              />
            ))}
            {filteredResults.length === 0 && activeBatch && (
              <div className="text-muted-foreground col-span-2 py-8 text-center text-xs">
                {filter
                  ? t.parallelAgents.noMatchingAgents
                  : t.parallelAgents.waitingForAgents}
              </div>
            )}
          </div>

          {/* Batch selector (if multiple batches) */}
          {batchCount > 1 && status && (
            <div className="flex items-center gap-1.5 border-t px-3 py-2">
              <span className="text-muted-foreground text-xs">
                {t.parallelAgents.batches}
              </span>
              {Object.entries(status.batches).map(([bid, bstatus]) => (
                <button
                  key={bid}
                  type="button"
                  onClick={() => fetchBatch(bid)}
                  className={cn(
                    "rounded-lg px-2 py-0.5 text-xs font-medium transition-colors",
                    activeBatch?.batch_id === bid
                      ? "bg-foreground/10 text-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {bid.slice(0, 6)}
                  <span
                    className={cn(
                      "ml-1 inline-block size-1.5 rounded-lg",
                      batchStatusDotClass(bstatus),
                    )}
                  />
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Global animation styles */}
      <style>{`
        @keyframes dotPulse {
          0%, 100% { opacity: 0.25; }
          50% { opacity: 1; }
        }
      `}</style>
    </div>
  );
}

export default ParallelAgentsPanel;
