/** Data types for the unified Task Board API. */

export type TaskType = "background" | "quest" | "scheduled" | "intelligence";

export type BoardStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "paused";

export interface UnifiedTask {
  id: string;
  type: TaskType;
  name: string;
  status: BoardStatus;
  phase: string;
  progress_pct: number;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number;
  error: string | null;
  extra: Record<string, unknown>;
}

export interface TaskBoardAllResponse {
  tasks: UnifiedTask[];
  total: number;
}

export interface TaskBoardStats {
  total: number;
  by_status: Record<string, number>;
  by_type: Record<string, number>;
  avg_duration_ms: number;
  success_rate: number;
  running_count: number;
  queued_count: number;
}

export interface TimelineTask {
  id: string;
  type: TaskType;
  name: string;
  status: BoardStatus;
  start_ms: number;
  end_ms: number;
  duration_ms: number;
  is_running: boolean;
}

export interface TaskBoardTimelineResponse {
  tasks: TimelineTask[];
  earliest_ms: number;
  latest_ms: number;
}

/** Column IDs for the kanban board. */
export const KANBAN_COLUMNS = [
  "queued",
  "running",
  "completed",
  "failed",
] as const;
export type KanbanColumnId = (typeof KANBAN_COLUMNS)[number];

/** Map every possible BoardStatus to a kanban column. */
export function statusToColumn(status: BoardStatus): KanbanColumnId {
  switch (status) {
    case "queued":
      return "queued";
    case "running":
      return "running";
    case "paused":
      return "running"; // show paused in the running column
    case "completed":
      return "completed";
    case "failed":
    case "cancelled":
      return "failed";
    default:
      return "queued";
  }
}
