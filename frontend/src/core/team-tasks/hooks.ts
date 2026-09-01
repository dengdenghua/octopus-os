import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createTask,
  deleteTask,
  getTaskProcessTimeline,
  listTasks,
  runTask,
  updateTask,
} from "./api";
import type {
  CreateTeamTaskInput,
  TeamTask,
  TeamTaskProcessTimeline,
  UpdateTeamTaskInput,
} from "./types";

const TEAM_TASKS_KEY = ["team-tasks"] as const;

export const teamTaskQueryKeys = {
  all: TEAM_TASKS_KEY,
  byRoom: (roomId?: string | null) =>
    [...TEAM_TASKS_KEY, "room", roomId ?? "all"] as const,
  processTimeline: (taskId?: string | null) =>
    [...TEAM_TASKS_KEY, "process-timeline", taskId ?? "none"] as const,
};

export function useTeamTasks(roomId?: string | null) {
  return useQuery<TeamTask[]>({
    queryKey: teamTaskQueryKeys.byRoom(roomId),
    queryFn: () => listTasks(roomId),
    enabled: roomId !== null,
    refetchInterval: (query) => {
      const tasks = query.state.data ?? [];
      return tasks.some((task) => task.status === "running") ? 1500 : false;
    },
  });
}

export function useTeamTaskProcessTimeline(
  taskId?: string | null,
  options: { enabled?: boolean; refetchMs?: number | false } = {},
) {
  return useQuery<TeamTaskProcessTimeline>({
    queryKey: teamTaskQueryKeys.processTimeline(taskId),
    queryFn: () => getTaskProcessTimeline(String(taskId)),
    enabled: Boolean(taskId) && (options.enabled ?? true),
    refetchInterval: options.refetchMs ?? false,
  });
}

export function useCreateTeamTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateTeamTaskInput) => createTask(input),
    onSuccess: (task) => {
      void qc.invalidateQueries({ queryKey: TEAM_TASKS_KEY });
      void qc.invalidateQueries({
        queryKey: teamTaskQueryKeys.byRoom(task.room_id),
      });
      void qc.invalidateQueries({
        queryKey: teamTaskQueryKeys.processTimeline(task.id),
      });
    },
  });
}

export function useUpdateTeamTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      taskId,
      input,
    }: {
      taskId: string;
      input: UpdateTeamTaskInput;
    }) => updateTask(taskId, input),
    onSuccess: (task) => {
      void qc.invalidateQueries({ queryKey: TEAM_TASKS_KEY });
      void qc.invalidateQueries({
        queryKey: teamTaskQueryKeys.byRoom(task.room_id),
      });
      void qc.invalidateQueries({
        queryKey: teamTaskQueryKeys.processTimeline(task.id),
      });
    },
  });
}

export function useDeleteTeamTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ taskId }: { taskId: string }) => deleteTask(taskId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: TEAM_TASKS_KEY });
      void qc.invalidateQueries({
        queryKey: [...TEAM_TASKS_KEY, "process-timeline"],
      });
    },
  });
}

export function useRunTeamTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ taskId }: { taskId: string }) => runTask(taskId),
    onSuccess: (task) => {
      void qc.invalidateQueries({ queryKey: TEAM_TASKS_KEY });
      void qc.invalidateQueries({
        queryKey: teamTaskQueryKeys.byRoom(task.room_id),
      });
      void qc.invalidateQueries({
        queryKey: teamTaskQueryKeys.processTimeline(task.id),
      });
    },
  });
}
