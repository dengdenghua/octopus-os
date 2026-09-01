import type { AIMessage } from "@/core/api/types";

export type SubtaskStatus =
  | "pending"
  | "reasoning"
  | "iterating"
  | "generating"
  | "analyzing"
  | "summarizing"
  | "in_progress"
  | "completed"
  | "failed"
  | "cancelled"
  | "timed_out";

export const SUBTASK_STATUS_LABELS: Record<SubtaskStatus, string> = {
  pending: "等待中",
  reasoning: "正在推理",
  iterating: "正在迭代",
  generating: "正在生成",
  analyzing: "正在分析",
  summarizing: "正在总结",
  in_progress: "执行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  timed_out: "已超时",
};

export const ACTIVE_SUBTASK_STATUSES: SubtaskStatus[] = [
  "reasoning",
  "iterating",
  "generating",
  "analyzing",
  "summarizing",
  "in_progress",
];

export function isSubtaskActive(status: SubtaskStatus): boolean {
  return ACTIVE_SUBTASK_STATUSES.includes(status);
}

export function isSubtaskTerminal(status: SubtaskStatus): boolean {
  return (
    status === "completed" ||
    status === "failed" ||
    status === "cancelled" ||
    status === "timed_out"
  );
}

export interface Subtask {
  id: string;
  status: SubtaskStatus;
  subagent_type?: string;
  description: string;
  latestMessage?: AIMessage;
  messages?: AIMessage[];
  prompt?: string;
  result?: string;
  error?: string;
  progress: number;
  avatarEmoji?: string;
  hue?: number;
  name?: string;
  role?: string;
  skills?: string[];
  tokenUsed?: number;
  tokenBudget?: number;
  /** Number of iterations this subagent has completed */
  iterationCount?: number;
  /** Files touched/modified by this subagent */
  filesTouched?: string[];
  /** Duration in milliseconds */
  duration?: number;
}
