import type { AgentRunState } from "@/components/workspace/agent-run-status";
import {
  isSubtaskActive,
  type Subtask,
  type SubtaskStatus,
} from "@/core/tasks/types";

export function subtaskRunState(status: SubtaskStatus): AgentRunState {
  if (status === "completed") return "done";
  if (status === "failed" || status === "timed_out" || status === "cancelled")
    return "error";
  if (status === "pending") return "waiting";
  if (isSubtaskActive(status)) return "running";
  return "pending";
}

export function subtaskProgress(
  task: Pick<Subtask, "status" | "progress">,
): number {
  if (
    task.status === "completed" ||
    task.status === "failed" ||
    task.status === "cancelled" ||
    task.status === "timed_out"
  ) {
    return 1;
  }
  if (task.status === "pending") return 0.08;
  return Math.max(0.18, Math.min(0.92, task.progress ?? 0.45));
}

/**
 * Returns a 0–100 integer percentage for display, or null when there is
 * no real progress to report. Only active tasks with a genuine progress
 * value (0 < progress < 1) get a number — the message-list replay carries
 * no live progress source (it only marks terminal states), so a numeric
 * fallback would fabricate a constant percentage. Callers render an
 * indeterminate bar (running) or status text (terminal) for null.
 */
export function subtaskProgressPercent(
  task: Pick<Subtask, "status" | "progress">,
): number | null {
  if (!isSubtaskActive(task.status)) return null;
  if (task.progress > 0 && task.progress < 1) {
    return Math.round(task.progress * 100);
  }
  return null;
}
