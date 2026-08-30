import { describe, expect, test } from "vitest";

// Test groupActivities in isolation without relying on the component's
// React imports.
import {
  extractThinkingText,
  groupActivities,
  isThinkingContentPart,
  MIN_AGGREGATION_SIZE,
} from "./message-grouping";

function aiToolCallMsg(
  id: string,
  name: string,
  args: any = {},
  tcId?: string,
) {
  return {
    id,
    type: "ai",
    content: "",
    tool_calls: [{ id: tcId ?? `tc-${id}`, name, args }],
  };
}

function thinkingMsg(id: string, thinking: string) {
  return {
    id,
    type: "ai",
    content: [{ type: "thinking", thinking }],
    tool_calls: [],
  };
}

function humanMsg(id: string, content = "hi") {
  return { id, type: "human", content };
}

function toolResultMsg(id: string, toolCallId: string, content = "ok") {
  return { id, type: "tool", content, tool_call_id: toolCallId };
}

describe("groupActivities", () => {
  test("returns empty for empty messages", () => {
    const result = groupActivities([]);
    expect(result).toEqual([]);
  });

  test("keeps conversationally worded thinking in the reasoning lane", () => {
    const chunks = groupActivities([
      thinkingMsg("thinking-1", "接下来我会检查项目配置。"),
      thinkingMsg("thinking-2", "然后我会运行测试。"),
    ] as any);

    expect(chunks).toHaveLength(1);
    expect(chunks[0]).toMatchObject({
      kind: "activity",
      activityKind: "think",
    });
  });

  test("aggregates continuous write_file calls into file_ops", () => {
    const messages = [
      humanMsg("h1", "write some files"),
      aiToolCallMsg("a1", "write_file", { path: "/src/a.ts", content: "x\ny" }),
      toolResultMsg("t1", "tc-a1"),
      aiToolCallMsg("a2", "write_file", { path: "/src/b.ts", content: "z" }),
      toolResultMsg("t2", "tc-a2"),
    ];
    const chunks = groupActivities(messages as any);
    // human passthrough, activity chunk for 2 file_ops
    expect(chunks.length).toBe(2);
    expect(chunks[0].kind).toBe("passthrough");
    expect(chunks[1].kind).toBe("activity");
    if (chunks[1].kind === "activity") {
      expect(chunks[1].activityKind).toBe("file_ops");
      expect(chunks[1].items.length).toBe(2);
      expect(chunks[1].items[0].label.includes("a.ts")).toBeTruthy();
    }
  });

  test("singleton tool call falls back to passthrough (below MIN)", () => {
    expect(MIN_AGGREGATION_SIZE).toBe(2);
    const messages = [
      humanMsg("h1"),
      aiToolCallMsg("a1", "write_file", { path: "/src/a.ts", content: "x" }),
    ];
    const chunks = groupActivities(messages as any);
    // Both merged into a passthrough since size < MIN.
    expect(chunks.length).toBe(1);
    expect(chunks[0].kind).toBe("passthrough");
  });

  test("mixed kinds cut the run", () => {
    const messages = [
      aiToolCallMsg("a1", "write_file", { path: "/a.ts", content: "x" }),
      aiToolCallMsg("a2", "write_file", { path: "/b.ts", content: "y" }),
      aiToolCallMsg("a3", "bash", { command: "ls -la" }),
      aiToolCallMsg("a4", "bash", { command: "pwd" }),
    ];
    const chunks = groupActivities(messages as any);
    expect(chunks.length).toBe(2);
    expect(chunks[0].kind).toBe("activity");
    expect(chunks[1].kind).toBe("activity");
    if (chunks[0].kind === "activity") {
      expect(chunks[0].activityKind).toBe("file_ops");
    }
    if (chunks[1].kind === "activity") {
      expect(chunks[1].activityKind).toBe("tool_calls");
    }
  });

  test("human message breaks a run", () => {
    const messages = [
      aiToolCallMsg("a1", "bash", { command: "ls" }),
      aiToolCallMsg("a2", "bash", { command: "pwd" }),
      humanMsg("h1", "stop"),
      aiToolCallMsg("a3", "bash", { command: "whoami" }),
      aiToolCallMsg("a4", "bash", { command: "date" }),
    ];
    const chunks = groupActivities(messages as any);
    expect(chunks.length).toBe(3);
    expect(chunks[0].kind).toBe("activity");
    expect(chunks[1].kind).toBe("passthrough");
    expect(chunks[2].kind).toBe("activity");
  });

  test("task tool falls through to passthrough (never aggregated)", () => {
    const messages = [
      aiToolCallMsg("a1", "task", { description: "Research X" }),
      aiToolCallMsg("a2", "task", { description: "Research Y" }),
    ];
    const chunks = groupActivities(messages as any);
    expect(chunks.length).toBe(1);
    expect(chunks[0].kind).toBe("passthrough");
  });

  test("present_files passthrough", () => {
    const messages = [
      aiToolCallMsg("a1", "present_files", { filepaths: ["/a.txt"] }),
    ];
    const chunks = groupActivities(messages as any);
    expect(chunks.length).toBe(1);
    expect(chunks[0].kind).toBe("passthrough");
  });

  test("file_ops label uses lines_added from args", () => {
    const messages = [
      aiToolCallMsg("a1", "write_file", {
        path: "/Hero.tsx",
        lines_added: 175,
      }),
      aiToolCallMsg("a2", "write_file", {
        path: "/Card.tsx",
        lines_added: 30,
      }),
    ];
    const chunks = groupActivities(messages as any);
    expect(chunks.length).toBe(1);
    if (chunks[0].kind === "activity") {
      // Label format is locale-dependent (en-US: "Write Hero.tsx +175 lines",
      // Implementation note.
      // markers: filename + "+<count>" appear regardless of locale.
      expect(chunks[0].items[0].label).toContain("Hero.tsx");
      expect(chunks[0].items[0].label).toContain("+175");
      expect(chunks[0].items[0].meta?.lines_added).toBe(175);
      expect(chunks[0].items[1].label).toContain("Card.tsx");
      expect(chunks[0].items[1].label).toContain("+30");
      expect(chunks[0].items[1].meta?.lines_added).toBe(30);
    }
  });

  test("bash label avoids raw command text", () => {
    const longCmd = "a".repeat(60);
    const messages = [
      aiToolCallMsg("a1", "bash", { command: longCmd }),
      aiToolCallMsg("a2", "bash", { command: "ls" }),
    ];
    const chunks = groupActivities(messages as any);
    expect(chunks.length).toBe(1);
    if (chunks[0].kind === "activity") {
      expect(chunks[0].items[0].label).toBe("Run checks");
      expect(chunks[0].items[0].label).not.toContain(longCmd);
      expect(chunks[0].items[1].label).not.toContain("ls");
    }
  });

  test("realtime shell tool names aggregate without labeling the raw command", () => {
    const messages = [
      aiToolCallMsg("a1", "exec_shell", {
        tool: "exec_shell",
        command: "exec_shell",
        inputPreview: "npm test",
      }),
      aiToolCallMsg("a2", "shell_command", { command: "npm run typecheck" }),
    ];
    const chunks = groupActivities(messages as any);
    expect(chunks.length).toBe(1);
    if (chunks[0].kind === "activity") {
      expect(chunks[0].activityKind).toBe("tool_calls");
      expect(chunks[0].items[0].label).toBe("Run checks");
      expect(chunks[0].items[1].label).toBe("Run checks");
      expect(chunks[0].items[0].label).not.toContain("npm test");
      expect(chunks[0].items[1].label).not.toContain("npm run typecheck");
    }
  });

  test("tool result message with error status marks item as error", () => {
    const messages = [
      aiToolCallMsg("a1", "bash", { command: "ls" }, "tc-a1"),
      {
        id: "t1",
        type: "tool",
        content: "boom",
        tool_call_id: "tc-a1",
        status: "error",
      },
      aiToolCallMsg("a2", "bash", { command: "pwd" }, "tc-a2"),
      toolResultMsg("t2", "tc-a2"),
    ];
    const chunks = groupActivities(messages as any);
    expect(chunks.length).toBe(1);
    if (chunks[0].kind === "activity") {
      expect(chunks[0].items[0].status).toBe("error");
      expect(chunks[0].items[1].status).toBe("done");
    }
  });

  test("running status when tool result missing", () => {
    const messages = [
      aiToolCallMsg("a1", "bash", { command: "ls" }, "tc-a1"),
      aiToolCallMsg("a2", "bash", { command: "pwd" }, "tc-a2"),
    ];
    const chunks = groupActivities(messages as any);
    if (chunks[0].kind === "activity") {
      expect(chunks[0].items[0].status).toBe("running");
    }
  });

  test("read tools classify as tool_calls", () => {
    const messages = [
      aiToolCallMsg("a1", "list_cwd", { path: "/src" }),
      aiToolCallMsg("a2", "read_text_file", { path: "/src/a.ts" }),
      aiToolCallMsg("a3", "grep", { pattern: "TODO" }),
    ];
    const chunks = groupActivities(messages as any);
    if (chunks[0].kind === "activity") {
      expect(chunks[0].activityKind).toBe("tool_calls");
    }
  });

  test("read tool aggregation uses public labels instead of raw tool names", () => {
    const messages = [
      aiToolCallMsg("a1", "read_file", { path: "/repo/src/chat.ts" }),
      aiToolCallMsg("a2", "grep", { pattern: "TODO" }),
      aiToolCallMsg("a3", "read_file", { path: "~/.ssh/id_rsa" }),
    ];
    const chunks = groupActivities(messages as any);
    expect(chunks.length).toBe(1);
    if (chunks[0].kind === "activity") {
      const labels = chunks[0].items.map((item) => item.label).join("\n");
      expect(labels).toContain("Read file: chat.ts");
      expect(labels).toContain("Search sources: TODO");
      expect(labels).toContain("Read file");
      expect(labels).not.toContain("read_file");
      expect(labels).not.toContain("grep ");
      expect(labels).not.toContain("/repo/src");
      expect(labels).not.toContain("id_rsa");
    }
  });

  test("realtime file edits classify as file_ops", () => {
    const messages = [
      aiToolCallMsg("a1", "edit_text_file", {
        path: "/a.ts",
        lines_added: 5,
        lines_removed: 2,
      }),
      aiToolCallMsg("a2", "write_text_file", { path: "/b.ts", lines_added: 1 }),
    ];
    const chunks = groupActivities(messages as any);
    if (chunks[0].kind === "activity") {
      expect(chunks[0].activityKind).toBe("file_ops");
      // Implementation note.
      // language-agnostic +/-count markers. Assert on the markers so
      // the test works regardless of which locale the component runs in.
      expect(chunks[0].items[0].label).toContain("+5");
      expect(chunks[0].items[0].label).toContain("-2");
      expect(chunks[0].items[1].label).toContain("+1");
    }
  });

  test("thinking-only messages aggregate as think", () => {
    const messages = [
      {
        id: "a1",
        type: "ai",
        content: [{ type: "thinking", thinking: "step 1" }],
      },
      {
        id: "a2",
        type: "ai",
        content: [{ type: "thinking", thinking: "step 2" }],
      },
    ];
    const chunks = groupActivities(messages as any);
    expect(chunks.length).toBe(1);
    if (chunks[0].kind === "activity") {
      expect(chunks[0].activityKind).toBe("think");
      expect(chunks[0].items.length).toBe(2);
    }
  });

  test("plan tool name routes to plan kind", () => {
    const messages = [
      aiToolCallMsg("a1", "execution_plan_propose", { title: "Step 1" }),
      aiToolCallMsg("a2", "update_plan", { step: "Step 2" }),
    ];
    const chunks = groupActivities(messages as any);
    if (chunks[0].kind === "activity") {
      expect(chunks[0].activityKind).toBe("plan");
    }
  });

  test("tool result orphans are routed to passthrough", () => {
    const messages = [
      humanMsg("h1"),
      { id: "t1", type: "tool", content: "x", tool_call_id: "unknown" },
    ];
    const chunks = groupActivities(messages as any);
    expect(chunks.length).toBe(1);
    expect(chunks[0].kind).toBe("passthrough");
  });

  test("chunk messageIndexes cover all inputs", () => {
    const messages = [
      humanMsg("h1"),
      aiToolCallMsg("a1", "bash", { command: "ls" }, "tc-a1"),
      toolResultMsg("t1", "tc-a1"),
      aiToolCallMsg("a2", "bash", { command: "pwd" }, "tc-a2"),
      toolResultMsg("t2", "tc-a2"),
      humanMsg("h2"),
    ];
    const chunks = groupActivities(messages as any);
    const seen = new Set<number>();
    for (const c of chunks) {
      for (const i of c.messageIndexes) {
        expect(!seen.has(i), `duplicate index ${i}`).toBeTruthy();
        seen.add(i);
      }
    }
    expect(seen.size).toBe(messages.length);
  });

  test("minSize=1 folds singletons", () => {
    const messages = [
      aiToolCallMsg("a1", "write_file", { path: "/a.ts", content: "x" }),
    ];
    const chunks = groupActivities(messages as any, 1);
    expect(chunks.length).toBe(1);
    expect(chunks[0].kind).toBe("activity");
  });
});

describe("isThinkingContentPart", () => {
  test("returns true for thinking parts", () => {
    expect(isThinkingContentPart({ type: "thinking", thinking: "x" })).toBe(
      true,
    );
    expect(isThinkingContentPart({ type: "thinking" })).toBe(true);
  });

  test("returns false for non-thinking shapes", () => {
    expect(isThinkingContentPart({ type: "text", text: "x" })).toBe(false);
    expect(isThinkingContentPart({ type: "image_url" })).toBe(false);
    expect(isThinkingContentPart(null)).toBe(false);
    expect(isThinkingContentPart(undefined)).toBe(false);
    expect(isThinkingContentPart("thinking")).toBe(false);
    expect(isThinkingContentPart(42)).toBe(false);
  });
});

describe("extractThinkingText", () => {
  test("returns empty string for plain text content", () => {
    expect(extractThinkingText("just a string")).toBe("");
  });

  test("joins multiple thinking blocks with newlines", () => {
    const content = [
      { type: "thinking", thinking: "step 1" },
      { type: "thinking", thinking: "step 2" },
    ];
    expect(extractThinkingText(content as any)).toBe("step 1\nstep 2");
  });

  test("skips non-thinking parts and trims the result", () => {
    const content = [
      { type: "text", text: "ignored" },
      { type: "thinking", thinking: "kept" },
    ];
    expect(extractThinkingText(content as any)).toBe("kept");
  });

  test("returns empty string when no thinking parts are present", () => {
    const content = [{ type: "text", text: "x" }];
    expect(extractThinkingText(content as any)).toBe("");
  });
});

describe("groupActivities file diffs", () => {
  test("carries unified diffs from edit_file args into file_ops meta", () => {
    const diff = [
      "--- a/src/a.ts",
      "+++ b/src/a.ts",
      "@@ -1,3 +1,4 @@",
      " const ctx = 1;",
      "-const oldLine = 2;",
      "+const newLine = 2;",
      "+const another = 3;",
    ].join("\n");
    const messages = [
      humanMsg("h1", "edit files"),
      aiToolCallMsg("a1", "edit_file", {
        path: "/src/a.ts",
        changes: [{ path: "/src/a.ts", op: "update", diff }],
      }),
      toolResultMsg("t1", "tc-a1"),
      aiToolCallMsg("a2", "write_file", { path: "/src/b.ts", content: "z" }),
      toolResultMsg("t2", "tc-a2"),
    ];
    const chunks = groupActivities(messages as any);
    const activity = chunks.find((c) => c.kind === "activity");
    expect(activity?.kind).toBe("activity");
    if (activity?.kind === "activity") {
      expect(activity.activityKind).toBe("file_ops");
      const first = activity.items[0];
      expect(Array.isArray(first.meta?.diffs)).toBe(true);
      const diffs = first.meta?.diffs as string[];
      expect(diffs[0]).toContain("+const newLine");
      expect(diffs[0]).toContain("-const oldLine");
    }
  });

  test("leaves meta.diffs absent when no diff is available", () => {
    const messages = [
      humanMsg("h1", "write"),
      aiToolCallMsg("a1", "write_file", { path: "/src/a.ts", content: "x" }),
      toolResultMsg("t1", "tc-a1"),
      aiToolCallMsg("a2", "write_file", { path: "/src/b.ts", content: "z" }),
      toolResultMsg("t2", "tc-a2"),
    ];
    const chunks = groupActivities(messages as any);
    const activity = chunks.find((c) => c.kind === "activity");
    expect(activity?.kind).toBe("activity");
    if (activity?.kind === "activity") {
      for (const item of activity.items) {
        expect(item.meta?.diffs).toBeUndefined();
      }
    }
  });
});

describe("groupActivities thinking visibility", () => {
  test("plan narration remains a think row regardless of wording", () => {
    const messages = [
      humanMsg("h1", "检查一下项目"),
      thinkingMsg("a1", "我将先检查项目的目录结构和依赖配置，确认技术栈。"),
      thinkingMsg("a2", "接下来我会查看 pyproject.toml 确认依赖声明。"),
    ];
    const chunks = groupActivities(messages as any);
    const activity = chunks.find((chunk) => chunk.kind === "activity");
    expect(activity?.kind).toBe("activity");
    if (activity?.kind === "activity") {
      expect(activity.activityKind).toBe("think");
      expect(activity.items).toHaveLength(2);
    }
  });

  test("inner chain-of-thought still folds into a think activity", () => {
    const messages = [
      humanMsg("h1", "为什么"),
      thinkingMsg(
        "a1",
        "用户可能期望 A，但方案 B 的成本更高，需要权衡影响面。",
      ),
      thinkingMsg("a2", "如果直接改接口会影响下游三个调用方，先评估风险。"),
    ];
    const chunks = groupActivities(messages as any);
    const activity = chunks.find((c) => c.kind === "activity");
    expect(activity?.kind).toBe("activity");
    if (activity?.kind === "activity") {
      expect(activity.activityKind).toBe("think");
      expect(activity.items.length).toBe(2);
    }
  });
});
