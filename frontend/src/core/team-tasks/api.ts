import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";

import type {
  CreateTeamTaskInput,
  DeleteTeamTaskResponse,
  ListTeamTasksResponse,
  TeamTask,
  TeamTaskProcessTimeline,
  TeamTaskProcessTimelineResponse,
  UpdateTeamTaskInput,
} from "./types";

const BASE = () => `${getBackendBaseURL()}/api/team-tasks`;

async function parseJson<T>(res: Response, action: string): Promise<T> {
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(
      `${action} failed: ${res.status}${detail ? ` ${detail}` : ` ${res.statusText}`}`,
    );
  }
  return (await res.json()) as T;
}

export async function listTasks(roomId?: string | null): Promise<TeamTask[]> {
  const qs = roomId ? `?room_id=${encodeURIComponent(roomId)}` : "";
  const res = await fetch(`${BASE()}${qs}`, { headers: authHeaders() });
  const data = await parseJson<ListTeamTasksResponse>(res, "List team tasks");
  return data.tasks;
}

export async function createTask(
  input: CreateTeamTaskInput,
): Promise<TeamTask> {
  const res = await fetch(BASE(), {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({
      ...input,
      description: input.description ?? "",
      sop_template: input.sop_template ?? "",
      assignees: input.assignees ?? [],
      metadata: input.metadata ?? {},
    }),
  });
  return parseJson<TeamTask>(res, "Create team task");
}

export async function updateTask(
  taskId: string,
  input: UpdateTeamTaskInput,
): Promise<TeamTask> {
  const res = await fetch(`${BASE()}/${encodeURIComponent(taskId)}`, {
    method: "PATCH",
    headers: jsonAuthHeaders(),
    body: JSON.stringify(input),
  });
  return parseJson<TeamTask>(res, "Update team task");
}

export async function deleteTask(
  taskId: string,
): Promise<DeleteTeamTaskResponse> {
  const res = await fetch(`${BASE()}/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  return parseJson<DeleteTeamTaskResponse>(res, "Delete team task");
}

export async function runTask(taskId: string): Promise<TeamTask> {
  const res = await fetch(`${BASE()}/${encodeURIComponent(taskId)}/run`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({}),
  });
  return parseJson<TeamTask>(res, "Run team task");
}

export async function getTaskProcessTimeline(
  taskId: string,
): Promise<TeamTaskProcessTimeline> {
  const res = await fetch(
    `${BASE()}/${encodeURIComponent(taskId)}/process-timeline`,
    { headers: authHeaders() },
  );
  const data = await parseJson<TeamTaskProcessTimelineResponse>(
    res,
    "Load team task process timeline",
  );
  return data.timeline;
}
