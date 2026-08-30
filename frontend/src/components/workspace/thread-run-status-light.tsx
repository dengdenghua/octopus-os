/**
 * ThreadRunStatusLight — extracted from `workspace-sidebar.tsx`
 * (P3 decomposition). Behavior-preserving move.
 */
import {
  agentRunStatusLightClass,
  agentRunStatusLightPulseClass,
} from "@/components/workspace/agent-run-status";
import { useI18n } from "@/core/i18n/hooks";
import type { ThreadRunStatus } from "@/core/threads/sidebar";
import { cn } from "@/lib/utils";

export function ThreadRunStatusLight({
  active,
  className,
  idle = "hidden",
  status,
}: {
  active?: boolean;
  className?: string;
  idle?: "hidden" | "queue";
  status?: ThreadRunStatus;
}) {
  const { t } = useI18n();
  if (!status) {
    if (idle === "hidden") return null;
    return (
      <span
        aria-hidden="true"
        className={cn(
          "relative inline-flex size-2 shrink-0 items-center justify-center rounded-full",
          active
            ? "border border-muted-foreground/40 bg-muted-foreground/60"
            : "border border-muted-foreground/60 bg-muted-foreground/20",
          className,
        )}
        data-thread-queue-indicator="idle"
      />
    );
  }
  const label =
    status === "running"
      ? t.sidebar.taskStatusRunning
      : status === "error"
        ? t.sidebar.taskStatusFailed
        : status === "waiting"
          ? t.agentWorkbench.waitingToContinue
          : t.sidebar.taskStatusPending;
  const colorClass = agentRunStatusLightClass(status);
  const pulseClass = agentRunStatusLightPulseClass(status);

  return (
    <span
      aria-label={label}
      role="img"
      title={label}
      className={cn(
        "relative inline-flex size-2 shrink-0 items-center justify-center rounded-full",
        className,
      )}
    >
      {pulseClass && (
        <span
          className={cn(
            "absolute inline-flex size-3 rounded-full opacity-25",
            colorClass,
            pulseClass,
          )}
        />
      )}
      <span
        className={cn(
          "relative inline-flex size-2 rounded-full shadow-[var(--shadow-xs)]",
          colorClass,
        )}
      />
    </span>
  );
}

/** One image cell · falls back to a colored initial circle if the
 *  backend has no avatar for the agent (404 on
 *  ``/api/agents/<id>/avatar``). The initial fallback uses a hash-based
 *  color so different agents don't all blend into the same grey. */
