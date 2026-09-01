import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteTask,
  listTasks,
  pauseTask,
  resumeTask,
  type PauseReason,
  type TasksListResponse,
} from "./api";

const TASKS_KEY = ["tasks"] as const;

export function getTasksRefetchInterval(
  data?: TasksListResponse,
): number | false {
  // `pending` contains pause requests, not queued/running work. Those records
  // can remain in storage for a long time, so treating them as hot keeps every
  // workspace polling forever. Only live tasks need the two-second cadence.
  const hasHot = (data?.active?.length ?? 0) > 0;
  return hasHot ? 2000 : false;
}

function tasksRefetchInterval(query: {
  state: { data?: TasksListResponse };
}): number | false {
  return getTasksRefetchInterval(query.state.data);
}

export function useTasks(status?: "paused" | "pending" | "active" | "all") {
  const statusValue = status ?? "all";
  return useQuery({
    queryKey: [...TASKS_KEY, statusValue],
    queryFn: ({ signal }) => listTasks(statusValue, signal),
    refetchInterval: tasksRefetchInterval,
    refetchIntervalInBackground: true,
    refetchOnReconnect: "always",
    refetchOnWindowFocus: "always",
    staleTime: 2000,
    gcTime: 30000,
  });
}

export function usePauseTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      taskId,
      reason = "user_request",
      note = "",
    }: {
      taskId: string;
      reason?: PauseReason;
      note?: string;
    }) => pauseTask(taskId, reason, note),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: TASKS_KEY });
    },
  });
}

export function useResumeTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      taskId,
      extra_iterations,
      extra_tokens,
      extra_usd,
    }: {
      taskId: string;
      extra_iterations?: number;
      extra_tokens?: number;
      extra_usd?: number;
    }) => resumeTask(taskId, { extra_iterations, extra_tokens, extra_usd }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: TASKS_KEY });
    },
  });
}

export function useDeleteTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ taskId }: { taskId: string }) => deleteTask(taskId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: TASKS_KEY });
    },
  });
}
