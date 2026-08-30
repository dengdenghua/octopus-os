import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";
import type { Team } from "@/core/teams";
import type { TeamTask } from "@/core/team-tasks";

import { TeamTasksPanel } from "./team-tasks-panel";

const fetchMock = vi.fn();

function teamFixture(): Team {
  return {
    id: "room-1",
    name: "Echo Lab",
    leaderId: "general",
    members: [
      {
        name: "general",
        display_name: "Eve",
        description: "Team lead",
        icon: null,
        avatar_url: null,
        model: null,
        tool_groups: null,
      },
    ],
    participants: [],
  };
}

function taskFixture(overrides: Partial<TeamTask> = {}): TeamTask {
  return {
    id: "task-1",
    room_id: "room-1",
    title: "timeline evidence smoke",
    description: "Prove team task replay evidence reaches the UI",
    sop_template: "",
    status: "done",
    assignees: [{ kind: "agent", ref: "general" }],
    created_by: "me",
    created_at: "2026-06-05T00:00:00Z",
    updated_at: "2026-06-05T00:01:00Z",
    started_at: "2026-06-05T00:00:10Z",
    completed_at: "2026-06-05T00:01:00Z",
    produced_artifacts: [
      {
        id: "artifact-1",
        type: "team_runner_output",
        title: "Final output",
        content: "final output for timeline evidence smoke",
      },
    ],
    metadata: {},
    ...overrides,
  };
}

function jsonResponse(data: unknown): Response {
  return new Response(JSON.stringify(data), {
    headers: { "Content-Type": "application/json" },
  });
}

describe("<TeamTasksPanel /> process timeline", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/team-tasks/task-1/process-timeline")) {
        return Promise.resolve(
          jsonResponse({
            timeline: {
              schema: "echo.team_task_process_timeline.v1",
              task_id: "task-1",
              room_id: "room-1",
              overview: {
                title: "timeline evidence smoke",
                status: "done",
                event_count: 3,
                artifact_count: 1,
                assignee_count: 1,
              },
              assignees: [{ kind: "agent", ref: "general" }],
              artifacts: [{ id: "artifact-1", type: "team_runner_output" }],
              timeline: [
                {
                  id: "run-started",
                  lane: "workflow",
                  kind: "run_started",
                  ts: "2026-06-05T00:00:10Z",
                  title: "Run started",
                  status: "running",
                  severity: "info",
                  summary: "TeamRunner started",
                },
                {
                  id: "artifact-1",
                  lane: "artifact",
                  kind: "team_runner_output",
                  ts: "2026-06-05T00:01:00Z",
                  title: "Produced artifact",
                  status: "ok",
                  severity: "info",
                  summary: "final output for timeline evidence smoke",
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
      }
      if (url.includes("/api/team-tasks")) {
        return Promise.resolve(
          jsonResponse({ tasks: [taskFixture()], count: 1 }),
        );
      }
      return Promise.resolve(jsonResponse({}));
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  test("opens persisted process evidence for a completed team task", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <TeamTasksPanel roomId="room-1" team={teamFixture()} />,
      { locale: "zh-CN" },
    );

    expect(
      await screen.findByText("timeline evidence smoke"),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /流程证据/ }));

    expect(await screen.findByText("Run started")).toBeInTheDocument();
    expect(screen.getByText("Produced artifact")).toBeInTheDocument();
    expect(screen.getByText("流程 3")).toBeInTheDocument();
    expect(screen.getByText("产物 1")).toBeInTheDocument();
    expect(screen.getByText("raw hidden")).toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/team-tasks/task-1/process-timeline",
        { headers: {} },
      ),
    );
  });
});
