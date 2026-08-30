import { useEffect, useMemo, useState } from "react";
import {
  AlertCircleIcon,
  ArrowRightIcon,
  BotIcon,
  CheckCircle2Icon,
  CirclePauseIcon,
  Clock3Icon,
  GitBranchIcon,
  Loader2Icon,
  MessageSquareTextIcon,
  PlayIcon,
  RefreshCwIcon,
  ShieldCheckIcon,
  XIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type {
  EchoTaskProjection,
  EchoTaskProjectionResponse,
  EchoTaskStatus,
} from "@/appliance/task-space";

type TaskFilter =
  | "all"
  | "active"
  | "waiting"
  | "recovery"
  | "completed"
  | "failed";

const STATUS: Record<
  string,
  { label: string; color: string; icon: typeof Clock3Icon }
> = {
  pending: { label: "准备中", color: "text-slate-500", icon: Clock3Icon },
  running: { label: "进行中", color: "text-blue-600", icon: Loader2Icon },
  waiting_approval: {
    label: "等待确认",
    color: "text-amber-600",
    icon: ShieldCheckIcon,
  },
  paused: { label: "已暂停", color: "text-slate-500", icon: CirclePauseIcon },
  verifying: { label: "验证中", color: "text-violet-600", icon: Loader2Icon },
  repairing: { label: "修复中", color: "text-orange-600", icon: Loader2Icon },
  completed: {
    label: "已完成",
    color: "text-emerald-600",
    icon: CheckCircle2Icon,
  },
  failed: { label: "失败", color: "text-red-600", icon: AlertCircleIcon },
  disconnected: {
    label: "已断开",
    color: "text-red-600",
    icon: AlertCircleIcon,
  },
  cancelled: { label: "已取消", color: "text-slate-500", icon: XIcon },
};

const FILTERS: Array<{ id: TaskFilter; label: string }> = [
  { id: "all", label: "全部" },
  { id: "active", label: "进行中" },
  { id: "waiting", label: "待确认" },
  { id: "recovery", label: "待恢复" },
  { id: "completed", label: "已完成" },
  { id: "failed", label: "异常" },
];

function statusMeta(status: EchoTaskStatus) {
  return STATUS[status] ?? STATUS.pending!;
}

function matches(task: EchoTaskProjection, filter: TaskFilter) {
  if (filter === "all") return true;
  if (filter === "active") {
    return ["pending", "running", "verifying", "repairing", "paused"].includes(
      task.displayStatus || task.status,
    );
  }
  if (filter === "waiting") return task.status === "waiting_approval";
  if (filter === "recovery") return Boolean(task.leaseHealth?.recoveryNeeded);
  if (filter === "completed") return task.status === "completed";
  return ["failed", "disconnected", "cancelled"].includes(task.status);
}

function relativeTime(value: string | null) {
  if (!value) return "";
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "";
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1_000));
  if (seconds < 60) return "刚刚";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

function TaskCard({
  task,
  selected,
  onSelect,
}: {
  task: EchoTaskProjection;
  selected: boolean;
  onSelect: () => void;
}) {
  const visibleStatus = task.displayStatus || task.status;
  const meta = statusMeta(visibleStatus);
  const StatusIcon = meta.icon;
  const latestCapability = task.capabilityDecisions.at(-1);
  return (
    <button
      type="button"
      aria-label={`查看任务：${task.title}`}
      onClick={onSelect}
      className={cn(
        "w-full rounded-2xl border bg-white/86 p-4 text-left shadow-sm transition hover:border-slate-300 hover:shadow-md",
        selected
          ? "border-blue-400 ring-2 ring-blue-500/15"
          : "border-slate-200/80",
      )}
    >
      <div className="flex items-start gap-3">
        <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 text-white shadow-sm">
          <BotIcon className="size-5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <h3 className="truncate text-sm font-semibold text-slate-900">
              {task.title}
            </h3>
            <span
              className={cn(
                "inline-flex items-center gap-1 text-[11px] font-medium",
                meta.color,
              )}
            >
              <StatusIcon
                className={cn(
                  "size-3.5",
                  ["running", "verifying", "repairing"].includes(
                    visibleStatus,
                  ) && "animate-spin",
                )}
              />
              {meta.label}
            </span>
          </div>
          {task.summary && (
            <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500">
              {task.summary}
            </p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] text-slate-400">
            {task.agentId && <span>{task.agentId}</span>}
            {task.mode && <span>· {task.mode}</span>}
            <span>· {relativeTime(task.updatedAt || task.startedAt)}</span>
          </div>
        </div>
      </div>

      {task.progressPercent !== null && task.status !== "completed" && (
        <div className="mt-3">
          <div className="mb-1 flex justify-between text-[10px] text-slate-400">
            <span>任务进度</span>
            <span>{Math.round(task.progressPercent)}%</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
            <div
              className="h-full rounded-full bg-gradient-to-r from-blue-500 to-indigo-500 transition-all"
              style={{ width: `${task.progressPercent}%` }}
            />
          </div>
        </div>
      )}

      {task.approval && (
        <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <div className="flex items-center gap-1.5 font-medium">
            <ShieldCheckIcon className="size-3.5" />
            需要管理员确认
          </div>
          <p className="mt-1 text-[11px] text-amber-700">
            {task.approval.reason ||
              task.approval.tool ||
              "高风险系统操作正在等待确认"}
          </p>
        </div>
      )}

      {task.leaseHealth?.recoveryNeeded && (
        <div className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
          <div className="flex items-center gap-1.5 font-medium">
            <AlertCircleIcon className="size-3.5" />
            上次执行已中断
          </div>
          <p className="mt-1 text-[11px] text-red-700">
            {task.leaseHealth.canResume
              ? "存在检查点，可在工作台接管并继续。"
              : "任务租约已失效，请在工作台检查后重新接管。"}
          </p>
        </div>
      )}

      {task.executionRecovery?.canStart && (
        <div className="mt-3 rounded-xl border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-800">
          <div className="flex items-center gap-1.5 font-medium">
            <PlayIcon className="size-3.5" />
            检查点已验证
          </div>
          <p className="mt-1 text-[11px] text-blue-700">
            可在原线程继续第 {task.executionRecovery.iteration ?? "?"}{" "}
            轮之后的执行。
          </p>
        </div>
      )}

      {latestCapability && (
        <div className="mt-3 flex items-center gap-2 rounded-lg bg-slate-50 px-2.5 py-2 text-[10px] text-slate-500">
          <ShieldCheckIcon className="size-3.5 text-slate-400" />
          <span className="font-medium text-slate-600">
            {latestCapability.capabilityId || latestCapability.target}
          </span>
          <span>· {latestCapability.outcome.toUpperCase()}</span>
          {latestCapability.risk && <span>· {latestCapability.risk}</span>}
        </div>
      )}
      <div className="mt-3 flex items-center justify-end gap-1 text-[10px] font-medium text-blue-600">
        查看详情
        <ArrowRightIcon className="size-3" />
      </div>
    </button>
  );
}

function TaskDetailDrawer({
  task,
  busy,
  actionError,
  actionMessage,
  onClose,
  onOpenWorkspace,
  onRequestTakeover,
  onRequestResume,
}: {
  task: EchoTaskProjection;
  busy: boolean;
  actionError: string | null;
  actionMessage: string | null;
  onClose: () => void;
  onOpenWorkspace: () => void;
  onRequestTakeover: () => void;
  onRequestResume: () => void;
}) {
  const visibleStatus = task.displayStatus || task.status;
  const meta = statusMeta(visibleStatus);
  const StatusIcon = meta.icon;
  const latestActivity = task.activity.slice(-8).reverse();
  return (
    <>
      <button
        type="button"
        aria-label="关闭任务详情"
        onClick={onClose}
        className="absolute inset-0 top-12 z-10 bg-slate-950/12 backdrop-blur-[1px]"
      />
      <aside
        aria-label="任务详情"
        className="absolute bottom-0 right-0 top-12 z-20 flex w-[min(410px,100%)] flex-col border-l border-slate-200 bg-white/96 shadow-2xl backdrop-blur-2xl"
      >
        <header className="flex shrink-0 items-start gap-3 border-b border-slate-200 px-5 py-4">
          <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 text-white">
            <BotIcon className="size-5" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="line-clamp-2 text-sm font-semibold text-slate-900">
              {task.title}
            </h2>
            <div
              className={cn("mt-1 flex items-center gap-1 text-xs", meta.color)}
            >
              <StatusIcon className="size-3.5" />
              {meta.label}
              <span className="text-slate-300">·</span>
              <span className="font-mono text-[10px] text-slate-400">
                {task.id.slice(0, 8)}
              </span>
            </div>
          </div>
          <button
            type="button"
            aria-label="收起任务详情"
            onClick={onClose}
            className="grid size-7 shrink-0 place-items-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          >
            <XIcon className="size-4" />
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
          {task.summary && (
            <section>
              <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                任务目标
              </div>
              <p className="mt-1.5 text-xs leading-5 text-slate-600">
                {task.summary}
              </p>
            </section>
          )}

          <section className="grid grid-cols-2 gap-2">
            {[
              ["Agent", task.agentId || "未记录"],
              ["模式", task.mode || "默认"],
              ["类型", task.kind || "task"],
              ["线程", task.threadId ? task.threadId.slice(0, 8) : "未关联"],
            ].map(([label, value]) => (
              <div key={label} className="rounded-xl bg-slate-50 px-3 py-2">
                <div className="text-[9px] text-slate-400">{label}</div>
                <div className="mt-0.5 truncate text-[11px] font-medium text-slate-700">
                  {value}
                </div>
              </div>
            ))}
          </section>

          {task.leaseHealth?.recoveryNeeded && (
            <section className="rounded-xl border border-red-200 bg-red-50 px-3 py-3">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-red-800">
                <GitBranchIcon className="size-3.5" />
                Agent 租约需要恢复
              </div>
              <p className="mt-1.5 text-[11px] leading-5 text-red-700">
                {task.leaseHealth.canResume
                  ? "Agent 找到了检查点。先接管任务租约，再回到原任务触发继续。"
                  : "当前没有可直接恢复的检查点。接管只会重新取得任务控制权，不会假装任务已经继续执行。"}
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5 text-[10px]">
                <span className="rounded-md bg-white/70 px-2 py-1 text-red-700">
                  租约 {task.leaseHealth.state}
                </span>
                {task.leaseHealth.recommendedAction && (
                  <span className="rounded-md bg-white/70 px-2 py-1 font-mono text-red-700">
                    {task.leaseHealth.recommendedAction}
                  </span>
                )}
              </div>
            </section>
          )}

          {task.executionRecovery?.checkpointAvailable && (
            <section className="rounded-xl border border-blue-200 bg-blue-50 px-3 py-3">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-blue-800">
                <PlayIcon className="size-3.5" />
                Agent 执行检查点
              </div>
              <p className="mt-1.5 text-[11px] leading-5 text-blue-700">
                {task.executionRecovery.reason}
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5 text-[10px]">
                <span className="rounded-md bg-white/70 px-2 py-1 text-blue-700">
                  第 {task.executionRecovery.iteration ?? "?"} 轮
                </span>
                {task.executionRecovery.phase && (
                  <span className="rounded-md bg-white/70 px-2 py-1 font-mono text-blue-700">
                    {task.executionRecovery.phase}
                  </span>
                )}
              </div>
            </section>
          )}

          {task.approval && (
            <section className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-3">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-amber-800">
                <ShieldCheckIcon className="size-3.5" />
                等待人工确认
              </div>
              <p className="mt-1.5 text-[11px] leading-5 text-amber-700">
                {task.approval.reason ||
                  "该任务需要在原任务中完成确认。涉及系统能力时仍必须经过设备密码与能力策略，任务空间不会旁路批准。"}
              </p>
            </section>
          )}

          {task.runtimeCapabilityGroups.length > 0 && (
            <section>
              <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                Agent 运行能力
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {task.runtimeCapabilityGroups.map((group) => (
                  <span
                    key={group}
                    className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1 font-mono text-[10px] text-slate-600"
                  >
                    {group}
                  </span>
                ))}
              </div>
            </section>
          )}

          {latestActivity.length > 0 && (
            <section>
              <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                最近系统活动
              </div>
              <div className="mt-2 space-y-2">
                {latestActivity.map((item) => (
                  <div
                    key={item.id}
                    className="rounded-lg border border-slate-100 bg-slate-50 px-2.5 py-2 text-[10px]"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium text-slate-700">
                        {item.capabilityId || item.action}
                      </span>
                      <span className="shrink-0 uppercase text-slate-400">
                        {item.outcome}
                      </span>
                    </div>
                    <div className="mt-0.5 truncate text-slate-400">
                      {item.target}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {actionError && (
            <div className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
              {actionError}
            </div>
          )}
          {actionMessage && (
            <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
              {actionMessage}
            </div>
          )}
        </div>

        <footer className="shrink-0 space-y-2 border-t border-slate-200 bg-slate-50/90 px-5 py-4">
          {task.executionRecovery?.canStart && (
            <button
              type="button"
              onClick={onRequestResume}
              disabled={busy}
              className="flex w-full items-center justify-center gap-2 rounded-xl bg-blue-600 px-3 py-2.5 text-xs font-semibold text-white transition hover:bg-blue-500 disabled:cursor-wait disabled:opacity-60"
            >
              {busy ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <PlayIcon className="size-4" />
              )}
              恢复执行并打开原任务…
            </button>
          )}
          {task.leaseHealth?.canTakeover && (
            <button
              type="button"
              onClick={onRequestTakeover}
              disabled={busy}
              className="flex w-full items-center justify-center gap-2 rounded-xl border border-red-200 bg-white px-3 py-2.5 text-xs font-semibold text-red-700 transition hover:bg-red-50 disabled:cursor-wait disabled:opacity-60"
            >
              {busy ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <GitBranchIcon className="size-4" />
              )}
              接管任务租约…
            </button>
          )}
          <button
            type="button"
            onClick={onOpenWorkspace}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-slate-900 px-3 py-2.5 text-xs font-semibold text-white transition hover:bg-slate-700"
          >
            <MessageSquareTextIcon className="size-4" />
            {task.threadId ? "打开原任务" : "打开工作台"}
          </button>
        </footer>
      </aside>
    </>
  );
}

export function TaskSpacePanel({
  open,
  projection,
  loading,
  error,
  onClose,
  onRefresh,
  onOpenWorkspace,
  onTakeover,
  onResumeExecution,
}: {
  open: boolean;
  projection: EchoTaskProjectionResponse | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onRefresh: () => void;
  onOpenWorkspace: (task?: EchoTaskProjection) => void;
  onTakeover: (taskId: string, reason: string) => Promise<unknown>;
  onResumeExecution: (taskId: string, reason: string) => Promise<unknown>;
}) {
  const [filter, setFilter] = useState<TaskFilter>("all");
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [takeoverCandidate, setTakeoverCandidate] =
    useState<EchoTaskProjection | null>(null);
  const [resumeCandidate, setResumeCandidate] =
    useState<EchoTaskProjection | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const tasks = useMemo(
    () => (projection?.tasks ?? []).filter((task) => matches(task, filter)),
    [filter, projection?.tasks],
  );
  const selectedTask =
    projection?.tasks.find((task) => task.id === selectedTaskId) ?? null;
  useEffect(() => {
    if (open) return;
    setSelectedTaskId(null);
    setTakeoverCandidate(null);
    setResumeCandidate(null);
    setActionError(null);
    setActionMessage(null);
  }, [open]);
  if (!open) return null;

  const confirmTakeover = async () => {
    if (!takeoverCandidate || actionBusy) return;
    setActionBusy(true);
    setActionError(null);
    setActionMessage(null);
    try {
      await onTakeover(
        takeoverCandidate.id,
        "设备管理员从 Echo 任务空间接管中断任务",
      );
      setActionMessage(
        "任务租约已接管。Agent 会重新核验检查点，再允许恢复执行。",
      );
      setTakeoverCandidate(null);
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "无法接管任务");
      setTakeoverCandidate(null);
    } finally {
      setActionBusy(false);
    }
  };

  const confirmResumeExecution = async () => {
    if (!resumeCandidate || actionBusy) return;
    const task = resumeCandidate;
    setActionBusy(true);
    setActionError(null);
    setActionMessage(null);
    try {
      await onResumeExecution(
        task.id,
        "设备管理员从 Echo 任务空间确认恢复检查点执行",
      );
      setResumeCandidate(null);
      onOpenWorkspace(task);
    } catch (reason) {
      setActionError(
        reason instanceof Error ? reason.message : "无法恢复任务执行",
      );
      setResumeCandidate(null);
    } finally {
      setActionBusy(false);
    }
  };

  const counts = projection?.counts;
  return (
    <div
      data-desktop-interactive
      className="fixed inset-0 z-[88] flex items-center justify-center bg-slate-950/18 p-4 backdrop-blur-[2px]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label="Echo 任务空间"
        className="relative flex h-[min(720px,calc(100vh-64px))] w-[min(920px,calc(100vw-32px))] flex-col overflow-hidden rounded-[22px] border border-white/75 bg-slate-50/94 text-slate-900 shadow-2xl shadow-slate-950/35 backdrop-blur-3xl"
      >
        <header className="flex h-12 shrink-0 items-center border-b border-slate-200/80 bg-white/70 px-4">
          <div className="flex gap-2">
            <button
              type="button"
              aria-label="关闭任务空间"
              onClick={onClose}
              className="grid size-3.5 place-items-center rounded-full bg-[#ff5f57] text-transparent hover:text-red-900/70"
            >
              <XIcon className="size-2.5" />
            </button>
            <span className="size-3.5 rounded-full bg-[#febc2e]" />
            <span className="size-3.5 rounded-full bg-[#28c840]" />
          </div>
          <div className="pointer-events-none absolute left-1/2 -translate-x-1/2 text-sm font-medium text-slate-600">
            任务空间
          </div>
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={onRefresh}
              aria-label="刷新任务"
              className="grid size-8 place-items-center rounded-lg text-slate-500 transition hover:bg-slate-200/70"
            >
              <RefreshCwIcon
                className={cn("size-4", loading && "animate-spin")}
              />
            </button>
            <button
              type="button"
              onClick={() => onOpenWorkspace()}
              className="rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-slate-700"
            >
              打开工作台
            </button>
          </div>
        </header>

        <div className="grid shrink-0 grid-cols-3 gap-3 border-b border-slate-200/70 px-5 py-4 sm:grid-cols-6">
          {(
            [
              ["全部", counts?.total ?? 0, "text-slate-800"],
              ["进行中", counts?.active ?? 0, "text-blue-600"],
              ["待确认", counts?.waitingApproval ?? 0, "text-amber-600"],
              ["待恢复", counts?.recoveryNeeded ?? 0, "text-orange-600"],
              ["异常", counts?.failed ?? 0, "text-red-600"],
              ["完成", counts?.completed ?? 0, "text-emerald-600"],
            ] satisfies Array<[string, number, string]>
          ).map(([label, value, color]) => (
            <div
              key={label}
              className="rounded-xl bg-white/75 px-3 py-2 text-center shadow-sm"
            >
              <div className={cn("text-lg font-semibold", color)}>{value}</div>
              <div className="text-[10px] text-slate-400">{label}</div>
            </div>
          ))}
        </div>

        <nav className="flex shrink-0 gap-1 border-b border-slate-200/70 px-5 py-2.5">
          {FILTERS.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setFilter(item.id)}
              className={cn(
                "rounded-lg px-3 py-1.5 text-xs transition",
                filter === item.id
                  ? "bg-slate-900 text-white"
                  : "text-slate-500 hover:bg-slate-200/70 hover:text-slate-800",
              )}
            >
              {item.label}
            </button>
          ))}
          <div className="ml-auto flex items-center gap-1.5 text-[10px] text-slate-400">
            <span
              className={cn(
                "size-2 rounded-full",
                projection?.auditIntegrity.ok === false
                  ? "bg-red-500"
                  : projection?.auditIntegrity.ok === true
                    ? "bg-emerald-500"
                    : "bg-slate-300",
              )}
            />
            审计链
          </div>
        </nav>

        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          {error && (
            <div className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">
              {error}
            </div>
          )}
          {!projection?.available && !loading ? (
            <div className="grid h-full place-items-center text-center">
              <div>
                <BotIcon className="mx-auto size-9 text-slate-300" />
                <p className="mt-3 text-sm font-medium text-slate-600">
                  任务服务正在初始化
                </p>
                <p className="mt-1 text-xs text-slate-400">
                  Echo 会直接读取 Echo Agent 的真实 TaskSupervisor，不创建副本。
                </p>
              </div>
            </div>
          ) : loading && !projection ? (
            <div className="grid h-full place-items-center text-slate-400">
              <Loader2Icon className="size-7 animate-spin" />
            </div>
          ) : tasks.length === 0 ? (
            <div className="grid h-full place-items-center text-center">
              <div>
                <CheckCircle2Icon className="mx-auto size-9 text-emerald-400" />
                <p className="mt-3 text-sm font-medium text-slate-600">
                  当前没有这类任务
                </p>
                <p className="mt-1 text-xs text-slate-400">
                  新的 Agent 任务会自动出现在这里。
                </p>
              </div>
            </div>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {tasks.map((task) => (
                <TaskCard
                  key={task.id}
                  task={task}
                  selected={selectedTaskId === task.id}
                  onSelect={() => {
                    setSelectedTaskId(task.id);
                    setActionError(null);
                    setActionMessage(null);
                  }}
                />
              ))}
            </div>
          )}
        </div>

        {selectedTask && (
          <TaskDetailDrawer
            task={selectedTask}
            busy={actionBusy}
            actionError={actionError}
            actionMessage={actionMessage}
            onClose={() => setSelectedTaskId(null)}
            onOpenWorkspace={() => onOpenWorkspace(selectedTask)}
            onRequestTakeover={() => setTakeoverCandidate(selectedTask)}
            onRequestResume={() => setResumeCandidate(selectedTask)}
          />
        )}

        {takeoverCandidate && (
          <div className="absolute inset-0 z-30 grid place-items-center bg-slate-950/30 p-5 backdrop-blur-sm">
            <section
              role="alertdialog"
              aria-modal="true"
              aria-label="确认接管任务"
              className="w-full max-w-sm rounded-2xl border border-white/80 bg-white p-5 shadow-2xl"
            >
              <div className="flex items-start gap-3">
                <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-red-50 text-red-600">
                  <GitBranchIcon className="size-5" />
                </div>
                <div>
                  <h2 className="text-sm font-semibold text-slate-900">
                    接管这个中断任务？
                  </h2>
                  <p className="mt-1 text-xs leading-5 text-slate-500">
                    Echo 将让当前 Agent worker 重新取得“
                    {takeoverCandidate.title}
                    ”的租约。这个动作不会自动执行任务；接管后仍需回到原任务确认并继续。
                  </p>
                </div>
              </div>
              <div className="mt-5 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setTakeoverCandidate(null)}
                  disabled={actionBusy}
                  className="rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-600 hover:bg-slate-50"
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={() => void confirmTakeover()}
                  disabled={actionBusy}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-3 py-2 text-xs font-semibold text-white hover:bg-red-500 disabled:cursor-wait disabled:opacity-60"
                >
                  {actionBusy && (
                    <Loader2Icon className="size-3.5 animate-spin" />
                  )}
                  确认接管
                </button>
              </div>
            </section>
          </div>
        )}

        {resumeCandidate && (
          <div className="absolute inset-0 z-30 grid place-items-center bg-slate-950/30 p-5 backdrop-blur-sm">
            <section
              role="alertdialog"
              aria-modal="true"
              aria-label="确认恢复任务执行"
              className="w-full max-w-sm rounded-2xl border border-white/80 bg-white p-5 shadow-2xl"
            >
              <div className="flex items-start gap-3">
                <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-blue-50 text-blue-600">
                  <PlayIcon className="size-5" />
                </div>
                <div>
                  <h2 className="text-sm font-semibold text-slate-900">
                    从 Agent 检查点继续？
                  </h2>
                  <p className="mt-1 text-xs leading-5 text-slate-500">
                    Echo 将在原线程恢复“{resumeCandidate.title}
                    ”，随后打开原任务查看实时进度。新的系统操作仍会按原规则等待人工确认，不会自动批准。
                  </p>
                </div>
              </div>
              <div className="mt-5 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setResumeCandidate(null)}
                  disabled={actionBusy}
                  className="rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-600 hover:bg-slate-50"
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={() => void confirmResumeExecution()}
                  disabled={actionBusy}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-xs font-semibold text-white hover:bg-blue-500 disabled:cursor-wait disabled:opacity-60"
                >
                  {actionBusy && (
                    <Loader2Icon className="size-3.5 animate-spin" />
                  )}
                  确认恢复
                </button>
              </div>
            </section>
          </div>
        )}
      </section>
    </div>
  );
}
