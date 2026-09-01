import {
  CheckCircle2Icon,
  CircleAlertIcon,
  CircleIcon,
  Loader2Icon,
  XCircleIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type { ParallelTaskStatus, TaskResult } from "@/core/parallel-agents/api";
import { cn } from "@/lib/utils";

interface ParallelTasksPanelProps {
  tasks: TaskResult[];
  className?: string;
}

const STATUS_ICON: Record<ParallelTaskStatus, typeof CircleIcon> = {
  pending: CircleIcon,
  running: Loader2Icon,
  completed: CheckCircle2Icon,
  failed: XCircleIcon,
  cancelled: XCircleIcon,
  timed_out: CircleAlertIcon,
  partial: CircleAlertIcon,
};

function statusTone(status: ParallelTaskStatus): string {
  if (status === "completed") return "text-primary";
  if (status === "failed" || status === "cancelled" || status === "timed_out")
    return "text-destructive";
  if (status === "running") return "text-primary";
  return "text-muted-foreground";
}

export function ParallelTasksPanel({
  tasks,
  className,
}: ParallelTasksPanelProps) {
  if (tasks.length === 0) {
    return (
      <div
        className={cn(
          "rounded-lg border border-dashed border-border px-6 py-10 text-center",
          className,
        )}
      >
        <p className="text-sm text-muted-foreground">No parallel tasks</p>
      </div>
    );
  }

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {tasks.map((task) => {
        const status = task.status as ParallelTaskStatus;
        const Icon = STATUS_ICON[status] ?? CircleIcon;
        return (
          <Card
            key={task.task_id}
            className="border border-border bg-card p-3 shadow-none"
          >
            <CardContent className="flex items-start gap-3 p-0">
              <Icon
                className={cn(
                  "mt-0.5 size-4 shrink-0",
                  statusTone(status),
                  status === "running" && "animate-spin",
                )}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium">
                    {task.description || task.task_id}
                  </span>
                  <Badge variant="outline" className="shrink-0 text-micro">
                    {task.subagent_name}
                  </Badge>
                </div>
                {task.error ? (
                  <p className="mt-1 line-clamp-2 text-xs text-destructive">
                    {task.error}
                  </p>
                ) : task.result ? (
                  <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                    {task.result}
                  </p>
                ) : null}
                {task.duration_seconds !== null && (
                  <p className="mt-1 text-micro text-muted-foreground">
                    {task.duration_seconds}s
                  </p>
                )}
              </div>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
