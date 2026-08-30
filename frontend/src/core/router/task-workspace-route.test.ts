import { describe, expect, test } from "vitest";

import { taskWorkspaceRoute } from "./task-workspace-route";

describe("task workspace route", () => {
  test("uses realtime new for the default agent", () => {
    expect(taskWorkspaceRoute()).toBe("/workspace/realtime/new");
    expect(taskWorkspaceRoute({ agentId: "general" })).toBe(
      "/workspace/realtime/new",
    );
  });

  test("carries non-default agent and prompt through query params", () => {
    expect(taskWorkspaceRoute({ agentId: "local codex" })).toBe(
      "/workspace/realtime/new?agent=local+codex",
    );
    expect(
      taskWorkspaceRoute({
        agentId: " coder ",
        prompt: " fix localhost/127 ",
      }),
    ).toBe("/workspace/realtime/new?prompt=fix+localhost%2F127&agent=coder");
  });

  test("carries a workspace path for a new workspace-bound task", () => {
    expect(
      taskWorkspaceRoute({
        workspacePath: "/Users/example/Public/echo-agent",
      }),
    ).toBe(
      "/workspace/realtime/new?workspace_path=%2FUsers%2Fexample%2FPublic%2Fecho-agent",
    );
  });
});
