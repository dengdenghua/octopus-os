import { describe, expect, it } from "vitest";

import {
  aggregateSimilarToolCalls,
  isAggregatedToolGroup,
} from "./activity-aggregator";

function makeTool(
  id: string,
  name: string,
  phaseId?: string,
  args: Record<string, unknown> = {},
) {
  return {
    id,
    type: "toolCall" as const,
    step: { name, args, phaseId, id },
    role: "execution" as const,
  };
}

function makeCommentary(id: string, text: string) {
  return {
    id,
    type: "commentary" as const,
    step: { commentary: text },
  };
}

describe("aggregateSimilarToolCalls", () => {
  it("passes through non-tool items unchanged", () => {
    const items = [makeCommentary("c1", "thinking...")];
    const result = aggregateSimilarToolCalls(items);
    expect(result).toHaveLength(1);
    expect(result[0].type).toBe("commentary");
  });

  it("does not aggregate single tool calls", () => {
    const items = [
      makeTool("t1", "edit_file", undefined, { file_path: "a.ts" }),
    ];
    const result = aggregateSimilarToolCalls(items);
    expect(result).toHaveLength(1);
    expect(result[0].type).toBe("toolCall");
  });

  it("aggregates consecutive same-kind tool calls", () => {
    const items = [
      makeTool("t1", "edit_file", "p1", { file_path: "a.ts" }),
      makeTool("t2", "edit_file", "p1", { file_path: "b.ts" }),
      makeTool("t3", "write_file", "p1", { path: "c.ts" }),
    ];
    const result = aggregateSimilarToolCalls(items);
    expect(result).toHaveLength(1);
    expect(isAggregatedToolGroup(result[0])).toBe(true);
    if (isAggregatedToolGroup(result[0])) {
      expect(result[0].count).toBe(3);
      expect(result[0].aggregateKind).toBe("file_write");
    }
  });

  it("breaks aggregation when kind changes", () => {
    const items = [
      makeTool("t1", "edit_file", undefined, { file_path: "a.ts" }),
      makeTool("t2", "edit_file", undefined, { file_path: "b.ts" }),
      makeTool("t3", "run_command", undefined, { command: "npm test" }),
    ];
    const result = aggregateSimilarToolCalls(items);
    expect(result).toHaveLength(2);
    expect(result[0].type).toBe("aggregatedToolGroup");
    expect(result[1].type).toBe("toolCall");
  });

  it("can fold mixed tool kinds into one conversational activity receipt", () => {
    const items = [
      makeTool("t1", "read_file", "p1", { file_path: "a.ts" }),
      makeTool("t2", "edit_file", "p1", { file_path: "b.ts" }),
      makeTool("t3", "run_command", "p1", { command: "pnpm test" }),
    ];
    const result = aggregateSimilarToolCalls(items, {
      groupMixedKinds: true,
    });
    expect(result).toHaveLength(1);
    expect(isAggregatedToolGroup(result[0])).toBe(true);
    if (isAggregatedToolGroup(result[0])) {
      expect(result[0].count).toBe(3);
      expect(result[0].aggregateKind).toBe("other");
    }
  });

  it("can hide internal phase boundaries inside one activity receipt", () => {
    const items = [
      makeTool("t1", "read_file", "inspect", { file_path: "a.ts" }),
      makeTool("t2", "edit_file", "implement", { file_path: "b.ts" }),
      makeTool("t3", "run_command", "verify", { command: "pnpm test" }),
    ];
    const result = aggregateSimilarToolCalls(items, {
      groupMixedKinds: true,
      groupAcrossPhases: true,
    });
    expect(result).toHaveLength(1);
    expect(isAggregatedToolGroup(result[0])).toBe(true);
  });

  it("breaks aggregation on non-tool items", () => {
    const items = [
      makeTool("t1", "edit_file", undefined, { file_path: "a.ts" }),
      makeCommentary("c1", "found the bug"),
      makeTool("t2", "edit_file", undefined, { file_path: "b.ts" }),
    ];
    const result = aggregateSimilarToolCalls(items);
    expect(result).toHaveLength(3);
    expect(result[0].type).toBe("toolCall");
    expect(result[1].type).toBe("commentary");
    expect(result[2].type).toBe("toolCall");
  });

  it("groups by phase", () => {
    const items = [
      makeTool("t1", "read_file", "explore", { file_path: "a.ts" }),
      makeTool("t2", "read_file", "explore", { file_path: "b.ts" }),
      makeTool("t3", "read_file", "implement", { file_path: "c.ts" }),
    ];
    const result = aggregateSimilarToolCalls(items);
    expect(result).toHaveLength(2);
    expect(isAggregatedToolGroup(result[0])).toBe(true);
    // A single item after a phase boundary is kept as a plain toolCall so the
    // transcript still has a clickable anchor for the new phase.
    expect(result[1].type).toBe("toolCall");
  });

  it("aggregates shell commands together", () => {
    const items = [
      makeTool("t1", "run_command", undefined, { command: "ls" }),
      makeTool("t2", "shell_command", undefined, { cmd: "npm install" }),
    ];
    const result = aggregateSimilarToolCalls(items);
    expect(result).toHaveLength(1);
    expect(isAggregatedToolGroup(result[0])).toBe(true);
    if (isAggregatedToolGroup(result[0])) {
      expect(result[0].aggregateKind).toBe("command");
      expect(result[0].count).toBe(2);
    }
  });

  it("separates reads from writes", () => {
    const items = [
      makeTool("t1", "read_file", undefined, { file_path: "a.ts" }),
      makeTool("t2", "edit_file", undefined, { file_path: "b.ts" }),
    ];
    const result = aggregateSimilarToolCalls(items);
    expect(result).toHaveLength(2);
    expect(result[0].type).toBe("toolCall");
    expect(result[1].type).toBe("toolCall");
  });

  it("folds shell file-reading workarounds into the file-read cluster", () => {
    const items = [
      makeTool("t1", "read_file", undefined, { file_path: "a.ts" }),
      makeTool("t2", "exec_shell", undefined, {
        command: "cat /path/to/b.ts",
      }),
      makeTool("t3", "exec_shell", undefined, {
        command: "sed -n '1,10p' /path/to/c.ts",
      }),
    ];
    const result = aggregateSimilarToolCalls(items);
    expect(result).toHaveLength(1);
    expect(isAggregatedToolGroup(result[0])).toBe(true);
    if (isAggregatedToolGroup(result[0])) {
      expect(result[0].aggregateKind).toBe("file_read");
      expect(result[0].count).toBe(3);
    }
  });

  it("keeps shell commands without a command argument as plain commands", () => {
    const items = [
      makeTool("t1", "exec_shell", undefined, {}),
      makeTool("t2", "exec_shell", undefined, { command: "ls" }),
    ];
    const result = aggregateSimilarToolCalls(items);
    expect(result).toHaveLength(1);
    expect(isAggregatedToolGroup(result[0])).toBe(true);
    if (isAggregatedToolGroup(result[0])) {
      expect(result[0].aggregateKind).toBe("command");
      expect(result[0].count).toBe(2);
    }
  });
});
