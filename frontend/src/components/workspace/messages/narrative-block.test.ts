import { describe, expect, test } from "vitest";

import { narrativeDurationMs, projectToolNarrative } from "./narrative-block";

describe("public narrative projection", () => {
  test("maps a tool into a human action and evidence reference", () => {
    const block = projectToolNarrative({
      id: "call-1",
      toolName: "edit_file",
      args: { path: "/workspace/auth.ts" },
      result: { status: "success" },
      phaseId: "phase-1",
      startedAt: 100,
      endedAt: 850,
    });

    expect(block.title).toBe("编辑");
    expect(block.object).toBe("auth.ts");
    expect(block.state).toBe("done");
    expect(block.evidenceRefs).toEqual([{ tab: "diff", eventId: "call-1" }]);
    expect(narrativeDurationMs(block)).toBe(750);
  });

  test("does not invent a fact while an action is still running", () => {
    const block = projectToolNarrative({
      id: "call-2",
      toolName: "run_command",
      args: { command: "npm test" },
    });

    expect(block.state).toBe("running");
    expect(block.fact).toBeNull();
    expect(narrativeDurationMs(block, 1200)).toBeNull();
  });
});
