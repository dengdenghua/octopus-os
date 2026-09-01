import { LoaderIcon, PauseIcon, PlayIcon, XIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  useDeleteTask,
  usePauseTask,
  useResumeTask,
  useTasks,
} from "@/core/tasks/hooks";
import type { PauseReason } from "@/core/tasks/api";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

interface Props {
  className?: string;
}

/* Implementation note. */
export function PausedTasksBanner({ className }: Props) {
  const { t } = useI18n();
  const b = t.pausedTasksBanner;
  const REASON_LABEL: Record<PauseReason, string> = {
    user_request: b.reasonUserRequest,
    budget_near_limit: b.reasonBudgetNearLimit,
    iteration_near_limit: b.reasonIterationNearLimit,
    model_spinning: b.reasonExternal,
    external: b.reasonExternal,
    client_disconnect: b.reasonExternal,
    approval_required: t.toolApproval.requiresApproval,
  } as Record<PauseReason, string>;
  const tasks = useTasks("all");
  const resume = useResumeTask();
  const remove = useDeleteTask();
  const pause = usePauseTask();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [budgetDialogTaskId, setBudgetDialogTaskId] = useState<string | null>(
    null,
  );
  const [dismissedBudgetDialogs, setDismissedBudgetDialogs] = useState<
    Set<string>
  >(() => new Set());
  const [extraTokensK, setExtraTokensK] = useState("100");
  const [extraUsd, setExtraUsd] = useState("0");
  const [extraIterations, setExtraIterations] = useState("15");

  // Implementation note.
  // Implementation note.
  // Implementation note.
  // Implementation note.
  useEffect(() => {
    const handler = () => {
      void qc.invalidateQueries({ queryKey: ["tasks"] });
    };
    window.addEventListener("echo:task_changed", handler);
    return () => window.removeEventListener("echo:task_changed", handler);
  }, [qc]);

  // Show both pending (just-requested, waiting for ReAct ack) and paused
  // (fully checkpointed). Pending is usually short-lived but surfacing
  // it avoids a dead-ack UI gap. Dedup by task_id so a request that
  // transitions pending→paused in one poll cycle doesn't flash twice.
  const { entries, activeVisible } = useMemo(() => {
    const pending = tasks.data?.pending ?? [];
    const paused = tasks.data?.paused ?? [];
    const active = tasks.data?.active ?? [];
    const byId = new Map<
      string,
      (typeof pending)[0] & { confirmed: boolean }
    >();
    for (const p of pending) byId.set(p.task_id, { ...p, confirmed: false });
    for (const p of paused) byId.set(p.task_id, { ...p, confirmed: true });
    return {
      entries: [...byId.values()],
      activeVisible: active.filter((a) => !byId.has(a.task_id)),
    };
  }, [tasks.data]);
  const budgetEntries = useMemo(
    () => entries.filter((entry) => entry.reason === "budget_near_limit"),
    [entries],
  );
  const budgetDialogTask =
    entries.find((entry) => entry.task_id === budgetDialogTaskId) ?? null;

  useEffect(() => {
    if (budgetDialogTaskId) return;
    const next = budgetEntries.find(
      (entry) => !dismissedBudgetDialogs.has(entry.task_id),
    );
    if (next) setBudgetDialogTaskId(next.task_id);
  }, [budgetDialogTaskId, budgetEntries, dismissedBudgetDialogs]);

  if (entries.length === 0 && activeVisible.length === 0) return null;

  async function handlePauseActive(
    taskId: string,
    _threadId: string,
    _agentId: string,
  ) {
    try {
      await pause.mutateAsync({ taskId, reason: "user_request" });
      toast.success(`${b.pauseRequestedPrefix} ${taskId.slice(0, 8)}…`, {
        description: b.pauseRequestedDesc,
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleResume(taskId: string, threadId: string) {
    try {
      await resume.mutateAsync({ taskId, extra_iterations: 15 });
      if (threadId) {
        navigate(`/workspace/realtime/${threadId}`);
        toast.success(`${b.resumedTitlePrefix} ${taskId.slice(0, 8)}…`, {
          description: b.resumedDescWithThread,
        });
      } else {
        toast.success(`${b.resumedTitleClearMark} ${taskId.slice(0, 8)}…`, {
          description: b.resumedDescNoThread,
        });
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleBudgetResume(taskId: string, threadId: string) {
    const tokensK = Math.max(0, Number.parseFloat(extraTokensK) || 0);
    const usd = Math.max(0, Number.parseFloat(extraUsd) || 0);
    const iterations = Math.max(0, Number.parseInt(extraIterations, 10) || 0);
    try {
      await resume.mutateAsync({
        taskId,
        extra_iterations: iterations,
        extra_tokens: Math.round(tokensK * 1000),
        extra_usd: usd,
      });
      setBudgetDialogTaskId(null);
      if (threadId) navigate(`/workspace/realtime/${threadId}`);
      toast.success(`${b.resumedTitlePrefix} ${taskId.slice(0, 8)}…`, {
        description: b.budgetResumedDesc,
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  function closeBudgetDialog() {
    if (budgetDialogTaskId) {
      setDismissedBudgetDialogs((prev) => {
        const next = new Set(prev);
        next.add(budgetDialogTaskId);
        return next;
      });
    }
    setBudgetDialogTaskId(null);
  }

  async function handleClear(taskId: string) {
    try {
      await remove.mutateAsync({ taskId });
      toast.success(`${b.clearedPrefix} ${taskId.slice(0, 8)}…`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div
      className={cn(
        "pointer-events-auto fixed bottom-4 right-4 z-50 flex max-w-sm flex-col gap-2",
        className,
      )}
    >
      {activeVisible.map((t) => (
        <div
          key={t.task_id}
          className="flex items-start gap-3 rounded-lg border border-info/30 bg-info/10 p-3 shadow-[var(--shadow-sm)]"
        >
          <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-info/20 text-info">
            <LoaderIcon className="h-3.5 w-3.5 animate-spin" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-sm font-medium">
              {b.executing}
              {t.max_iterations > 0 && (
                <span className="rounded-md border border-border-default bg-background/60 px-1.5 py-0.5 text-xs font-mono font-normal text-muted-foreground">
                  {t.current_iteration}/{t.max_iterations}
                </span>
              )}
              <span className="rounded-md border border-border-default bg-background/60 px-1.5 py-0.5 text-xs font-mono font-normal text-muted-foreground">
                {t.task_id.slice(0, 10)}
              </span>
            </div>
            {(t.tokens_spent > 0 ||
              (t.context_capacity_tokens ?? 0) > 0 ||
              t.max_usd > 0) && (
              <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
                {(t.context_capacity_tokens ?? 0) > 0 && (
                  <span>
                    ctx{" "}
                    <span className="font-mono">
                      {((t.current_context_tokens ?? 0) / 1000).toFixed(1)}k /
                      {((t.context_capacity_tokens ?? 0) / 1000).toFixed(0)}k
                    </span>
                    {(t.context_utilization ?? 0) >= 0.7 ? " ⚠" : ""}
                  </span>
                )}
                {t.tokens_spent > 0 && (
                  <span title={b.tokensLabel}>
                    Σ{" "}
                    <span className="font-mono">
                      {(t.tokens_spent / 1000).toFixed(1)}k
                    </span>
                  </span>
                )}
                {t.max_usd > 0 && (
                  <span>
                    {b.costLabel}{" "}
                    <span className="font-mono">
                      ${t.cost_usd.toFixed(3)}/${t.max_usd.toFixed(2)}
                    </span>
                  </span>
                )}
              </div>
            )}
            {t.agent_id && (
              <div className="mt-0.5 text-xs text-muted-foreground">
                {b.agentLabel} {t.agent_id}
              </div>
            )}
            {t.thread_id && (
              <div className="mt-0.5 text-xs text-muted-foreground font-mono truncate">
                {b.threadLabel} {t.thread_id.slice(0, 16)}…
              </div>
            )}
            <div className="mt-2 flex gap-2">
              <Button
                size="sm"
                variant="outline"
                className="h-7 gap-1 text-xs"
                onClick={() =>
                  handlePauseActive(t.task_id, t.thread_id, t.agent_id)
                }
                disabled={pause.isPending}
              >
                <PauseIcon className="h-3 w-3" />
                {b.pauseBtn}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 gap-1 text-xs text-muted-foreground"
                onClick={() => remove.mutate({ taskId: t.task_id })}
                disabled={remove.isPending}
              >
                <XIcon className="h-3 w-3" />
              </Button>
            </div>
          </div>
        </div>
      ))}
      {entries.map((req) => (
        <div
          key={req.task_id}
          className="flex items-start gap-3 rounded-lg border border-warning/60 bg-warning/5 p-3 shadow-[var(--shadow-sm)] dark:border-warning/40"
        >
          <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-warning/15 text-warning dark:bg-warning/50">
            <PauseIcon className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-sm font-medium">
              {req.confirmed ? b.paused : b.pendingPause}
              <span className="rounded-md border border-border-default bg-background/60 px-1.5 py-0.5 text-xs font-mono font-normal text-muted-foreground">
                {req.task_id.slice(0, 10)}
              </span>
            </div>
            <div className="mt-0.5 text-xs text-muted-foreground">
              {REASON_LABEL[req.reason] ?? req.reason}
              {req.note ? ` · ${req.note}` : ""}
            </div>
            {req.thread_id ? (
              <div className="mt-0.5 text-xs text-muted-foreground font-mono truncate">
                {b.threadLabel} {req.thread_id.slice(0, 16)}…
              </div>
            ) : null}
            <div className="mt-2 flex gap-2">
              <Button
                size="sm"
                className="h-7 gap-1 text-xs"
                onClick={() =>
                  req.reason === "budget_near_limit"
                    ? setBudgetDialogTaskId(req.task_id)
                    : handleResume(req.task_id, req.thread_id)
                }
                disabled={resume.isPending}
              >
                <PlayIcon className="h-3 w-3" />
                {b.continueBtn}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 gap-1 text-xs text-muted-foreground"
                onClick={() => handleClear(req.task_id)}
                disabled={remove.isPending}
                title={b.clearTitle}
              >
                <XIcon className="h-3 w-3" />
                {b.clearBtn}
              </Button>
            </div>
          </div>
        </div>
      ))}
      <Dialog
        open={Boolean(budgetDialogTask)}
        onOpenChange={(open) => {
          if (!open) closeBudgetDialog();
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{b.budgetDialogTitle}</DialogTitle>
            <DialogDescription>{b.budgetDialogDesc}</DialogDescription>
          </DialogHeader>
          {budgetDialogTask ? (
            <div className="space-y-3">
              <div className="rounded-md border border-border-default bg-muted/35 px-3 py-2 text-xs text-muted-foreground">
                <div className="font-mono text-xs">
                  {budgetDialogTask.task_id.slice(0, 10)}
                </div>
                {budgetDialogTask.note ? (
                  <div className="mt-1">{budgetDialogTask.note}</div>
                ) : null}
              </div>
              <div className="grid grid-cols-3 gap-2">
                <label className="space-y-1 text-xs text-muted-foreground">
                  <span>{b.extraTokensKLabel}</span>
                  <Input
                    value={extraTokensK}
                    onChange={(event) => setExtraTokensK(event.target.value)}
                    inputMode="numeric"
                    className="h-8"
                  />
                </label>
                <label className="space-y-1 text-xs text-muted-foreground">
                  <span>{b.extraUsdLabel}</span>
                  <Input
                    value={extraUsd}
                    onChange={(event) => setExtraUsd(event.target.value)}
                    inputMode="decimal"
                    className="h-8"
                  />
                </label>
                <label className="space-y-1 text-xs text-muted-foreground">
                  <span>{b.extraIterationsLabel}</span>
                  <Input
                    value={extraIterations}
                    onChange={(event) => setExtraIterations(event.target.value)}
                    inputMode="numeric"
                    className="h-8"
                  />
                </label>
              </div>
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="ghost" onClick={closeBudgetDialog}>
              {b.notNowBtn}
            </Button>
            <Button
              onClick={() =>
                budgetDialogTask &&
                handleBudgetResume(
                  budgetDialogTask.task_id,
                  budgetDialogTask.thread_id,
                )
              }
              disabled={resume.isPending || !budgetDialogTask}
            >
              <PlayIcon className="h-3 w-3" />
              {b.continueWithBudgetBtn}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
