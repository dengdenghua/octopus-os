import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AgentTraceTaskRecoveryQueue } from "@/core/agent-trace/api";
import { cn } from "@/lib/utils";
import { GateStat } from "../../replay-panel";
import { GitBranchIcon } from "lucide-react";
import { countRecovery, shortId, taskRecoveryActionLabel, taskRecoveryHint, taskRecoverySteps } from "../operator-utils";
import { useOperatorCopy } from "../use-operator-copy";

export function TaskRecoveryQueueCard({
  queue,
  busyId,
  onTakeover,
}: {
  queue: AgentTraceTaskRecoveryQueue;
  busyId: string | null;
  onTakeover: (taskId: string) => void;
}) {
  const to = useOperatorCopy();
  const actionable = queue.items.filter(
    (item) => item.recommended_action !== "monitor",
  );
  const topItem = actionable[0] ?? queue.items[0];
  const healthy = actionable.length === 0;
  return (
    <div
      className={cn(
        "mt-3 rounded-lg border px-3 py-2",
        healthy
          ? "border-success/25 bg-success/10"
          : "border-warning/30 bg-warning/10",
      )}
    >
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
            <GitBranchIcon
              className={cn(
                "size-4",
                healthy
                  ? "text-success"
                  : "text-warning",
              )}
            />
            {to("Task recovery queue")}
            <Badge variant="outline" className="text-xs">
              {queue.total} {to("tracked")}
            </Badge>
            <Badge
              variant={healthy ? "outline" : "destructive"}
              className="text-xs"
            >
              {actionable.length} {to("action")}
            </Badge>
          </div>
          <div className="mt-1 truncate text-xs text-muted-foreground">
            {topItem
              ? `${to(taskRecoveryActionLabel(topItem.recommended_action))} · ${topItem.title || topItem.task_id}`
              : to("No stalled, failed, or approval-blocked task runs.")}
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-right font-mono text-xs">
          <GateStat label={to("shown")} value={queue.count} />
          <GateStat
            label={to("takeover")}
            value={countRecovery(queue, "takeover")}
          />
          <GateStat
            label={to("resume")}
            value={countRecovery(queue, "resume")}
          />
        </div>
      </div>
      {queue.items.length > 0 && (
        <div className="mt-2 grid gap-2 lg:grid-cols-2">
          {queue.items.slice(0, 4).map((item) => {
            const busy = busyId === `takeover-task:${item.task_id}`;
            const steps = taskRecoverySteps(item);
            const checkpointId =
              item.checkpoint_id ||
              item.resume_checkpoint_id ||
              item.latest_checkpoint_id ||
              "available";
            return (
              <div
                key={item.task_id}
                className="rounded-md border border-background/70 bg-background/55 px-2 py-2"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-xs font-medium">
                      {item.title || item.task_id}
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                      <span className="font-mono">{shortId(item.task_id)}</span>
                      <span>{item.status ?? to("unknown")}</span>
                      {item.kind && <span>{item.kind}</span>}
                      {item.lease_health?.state && (
                        <span>
                          {to("lease")} {item.lease_health.state}
                        </span>
                      )}
                    </div>
                  </div>
                  <Badge variant="outline" className="shrink-0 text-xs">
                    P{item.priority}
                  </Badge>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <Badge
                    variant={
                      item.recommended_action === "monitor"
                        ? "outline"
                        : "secondary"
                    }
                    className="text-xs"
                  >
                    {to(taskRecoveryActionLabel(item.recommended_action))}
                  </Badge>
                  {item.has_checkpoint && (
                    <Badge variant="outline" className="text-xs">
                      {to("checkpoint")} {shortId(checkpointId)}
                    </Badge>
                  )}
                  {item.thread_id && (
                    <Badge variant="outline" className="text-xs">
                      {to("thread")} {shortId(item.thread_id)}
                    </Badge>
                  )}
                </div>
                <div className="mt-2 flex items-center justify-between gap-2">
                  <div className="min-w-0 text-xs text-muted-foreground">
                    <div>
                      {item.can_resume
                        ? to("Resume-safe state is available")
                        : item.can_takeover
                          ? to("Lease can be reclaimed")
                          : to(taskRecoveryHint(item.recommended_action))}
                    </div>
                    {steps.length > 0 && (
                      <div className="mt-0.5 truncate font-mono">
                        {steps.join(" -> ")}
                      </div>
                    )}
                  </div>
                  {item.can_takeover && (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-7 shrink-0 px-2 text-xs"
                      disabled={busy}
                      onClick={() => onTakeover(item.task_id)}
                    >
                      <GitBranchIcon className="mr-1 size-3" />
                      {to("Take over")}
                    </Button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
