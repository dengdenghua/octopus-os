import { LoaderIcon, PauseIcon } from "lucide-react";
import { memo } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { usePauseTask, useTasks } from "@/core/tasks/hooks";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

interface Props {
  threadId: string;
  className?: string;
}

/* Implementation note. */
export const ReactProgressPill = memo(function ReactProgressPill({
  threadId,
  className,
}: Props) {
  const { t } = useI18n();
  const b = t.pausedTasksBanner;
  const tasks = useTasks("active");
  const pause = usePauseTask();

  const active = (tasks.data?.active ?? []).find(
    (a) => a.thread_id === threadId,
  );
  if (!active) return null;

  async function handlePause() {
    if (!active) return;
    try {
      await pause.mutateAsync({
        taskId: active.task_id,
        reason: "user_request",
      });
      toast.success(
        `${b.pauseRequestedPrefix} ${active.task_id.slice(0, 8)}…`,
        {
          description: b.pauseRequestedDesc,
        },
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  const contextPct =
    active.context_capacity_tokens && active.context_capacity_tokens > 0
      ? ((active.current_context_tokens ?? 0) /
          active.context_capacity_tokens) *
        100
      : 0;
  const iterPct =
    active.max_iterations > 0
      ? (active.current_iteration / active.max_iterations) * 100
      : 0;
  const hot = contextPct >= 70 || iterPct >= 70;

  return (
    <div
      className={cn(
        "flex items-center gap-1.5 rounded-full border border-border-default bg-card/50 px-2 py-0.5 text-xs",
        hot
          ? "border-warning/60 bg-warning/5 dark:border-warning/40"
          : "border-info/30 bg-info/10",
        className,
      )}
    >
      <LoaderIcon
        className={cn(
          "h-3 w-3 animate-spin",
          hot ? "text-warning" : "text-info",
        )}
      />
      {active.max_iterations > 0 && (
        <span className="font-mono text-muted-foreground">
          iter {active.current_iteration}/{active.max_iterations}
        </span>
      )}
      {!!active.context_capacity_tokens && (
        <span className="font-mono text-muted-foreground">
          · ctx {((active.current_context_tokens ?? 0) / 1000).toFixed(1)}k/
          {(active.context_capacity_tokens / 1000).toFixed(0)}k
        </span>
      )}
      {active.tokens_spent > 0 && (
        <span className="font-mono text-muted-foreground" title={b.tokensLabel}>
          · Σ{(active.tokens_spent / 1000).toFixed(1)}k
        </span>
      )}
      <Button
        size="sm"
        variant="ghost"
        className="ml-0.5 h-5 gap-0.5 rounded-full px-1.5 text-xs hover:bg-background/60"
        onClick={handlePause}
        disabled={pause.isPending}
        title={b.pauseBtn}
      >
        <PauseIcon className="h-3 w-3" />
        {b.pauseBtn}
      </Button>
    </div>
  );
});
