import { beforeEach, describe, expect, test, vi } from "vitest";

import {
  createTask,
  deleteTask,
  getTaskProcessTimeline,
  listTasks,
  runTask,
  updateTask,
} from "./api";
import type { TeamTask } from "./types";

const fetchMock = vi.fn();

function task(overrides: Partial<TeamTask> = {}): TeamTask {
  return {
    id: "task-1",
    room_id: "room-1",
    title: "Draft plan",
    description: "",
    sop_template: "",
    status: "pending",
    assignees: [],
    created_by: "local",
    created_at: "2026-06-05T00:00:00Z",
    updated_at: "2026-06-05T00:00:00Z",
    started_at: null,
    completed_at: null,
    produced_artifacts: [],
    metadata: {},
    ...overrides,
  };
}

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("team task api", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  test("lists tasks scoped by room id", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ tasks: [task()], count: 1 }),
    );

    const tasks = await listTasks("room/a b");

    expect(tasks).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/team-tasks?room_id=room%2Fa%20b",
      { headers: {} },
    );
  });

  test("creates tasks with backend wire fields and defaults", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(task({ title: "Run SOP" })));

    await createTask({ room_id: "room-1", title: "Run SOP" });

    expect(fetchMock).toHaveBeenCalledWith("/api/team-tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        room_id: "room-1",
        title: "Run SOP",
        description: "",
        sop_template: "",
        assignees: [],
        metadata: {},
      }),
    });
  });

  test("updates, deletes, and runs by task id", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(task({ status: "running" })))
      .mockResolvedValueOnce(
        jsonResponse({ ok: true, deleted: true, task_id: "task/1" }),
      )
      .mockResolvedValueOnce(jsonResponse(task({ status: "running" })));

    await updateTask("task/1", { status: "running" });
    await deleteTask("task/1");
    await runTask("task/1");

    expect(fetchMock.mock.calls[0]).toEqual([
      "/api/team-tasks/task%2F1",
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "running" }),
      },
    ]);
    expect(fetchMock.mock.calls[1]).toEqual([
      "/api/team-tasks/task%2F1",
      { method: "DELETE", headers: {} },
    ]);
    expect(fetchMock.mock.calls[2]).toEqual([
      "/api/team-tasks/task%2F1/run",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      },
    ]);
  });

  test("loads the persisted process timeline by task id", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        timeline: {
          schema: "echo.team_task_process_timeline.v1",
          task_id: "task/1",
          room_id: "room-1",
          overview: {
            title: "Draft plan",
            status: "done",
            event_count: 3,
            artifact_count: 1,
            assignee_count: 2,
          },
          assignees: [],
          artifacts: [],
          timeline: [
            {
              id: "run-started",
              lane: "workflow",
              kind: "run_started",
              ts: "2026-06-05T00:00:00Z",
              title: "Run started",
            },
          ],
          safety: {
            raw_messages_included: false,
            artifact_content_truncated: true,
            process_events_persisted: true,
            process_event_limit: 300,
          },
        },
      }),
    );

    const timeline = await getTaskProcessTimeline("task/1");

    expect(timeline.schema).toBe("echo.team_task_process_timeline.v1");
    expect(timeline.timeline[0].kind).toBe("run_started");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/team-tasks/task%2F1/process-timeline",
      { headers: {} },
    );
  });

  test("throws response details on failure", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response("missing run endpoint", { status: 404 }),
    );

    await expect(runTask("task-1")).rejects.toThrow(
      "Run team task failed: 404 missing run endpoint",
    );
  });
});
