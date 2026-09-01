import { describe, expect, test } from "vitest";

import type { AIMessage, Message } from "@/core/api/types";

import { formatThreadAsJSON, formatThreadAsMarkdown } from "./export";
import type { AgentThread } from "./types";

const thread: AgentThread = {
  thread_id: "thread-1",
  status: "idle",
  created_at: "2026-01-01T00:00:00.000Z",
  updated_at: "2026-01-01T00:00:00.000Z",
  metadata: { title: "导出测试" },
  values: {
    title: "导出测试",
    messages: [],
    artifacts: [],
  },
};

function human(id: string, content: string): Message {
  return { id, type: "human", content };
}

function ai(partial: Partial<AIMessage> & { id: string }): AIMessage {
  return {
    type: "ai",
    content: "",
    ...partial,
  };
}

describe("thread export", () => {
  test("exports markdown as a clean conversation without reasoning or tool call internals", () => {
    const markdown = formatThreadAsMarkdown(thread, [
      human("u1", "只读比较两个文件"),
      ai({
        id: "a1",
        content: "我先核对实现。<read_only> </read_only> 结论一致。",
        additional_kwargs: {
          reasoning_content: "private chain of thought",
        },
        tool_calls: [
          {
            id: "read-1",
            name: "read_file",
            args: { path: "src/app.tsx" },
          },
        ],
      }),
      ai({
        id: "a2",
        content: "<read_only> </read_only>",
      }),
    ]);

    expect(markdown).toContain("我先核对实现。 结论一致。");
    expect(markdown).not.toContain("read_only");
    expect(markdown).not.toContain("read_file");
    expect(markdown).not.toContain("Tool:");
    expect(markdown).not.toContain("<details>");
    expect(markdown).not.toContain("Thinking");
    expect(markdown).not.toContain("private chain of thought");
  });

  test("exports json as sanitized user-visible messages only", () => {
    const json = formatThreadAsJSON(thread, [
      human("u1", "看一下"),
      ai({
        id: "a1",
        content: "摘要显示为 `<TextBlock>`，不是协议。",
        additional_kwargs: {
          reasoning_content: "private chain of thought",
        },
        tool_calls: [
          {
            id: "search-1",
            name: "web_search",
            args: { query: "secret query" },
          },
        ],
      }),
      ai({
        id: "a2",
        content: "<read_only> </read_only>",
      }),
    ]);
    const parsed = JSON.parse(json) as {
      messages: Array<Record<string, unknown>>;
    };

    expect(parsed.messages).toEqual([
      { type: "human", id: "u1", content: "看一下" },
      { type: "ai", id: "a1", content: "摘要显示为，不是协议。" },
    ]);
    expect(json).not.toContain("tool_calls");
    expect(json).not.toContain("web_search");
    expect(json).not.toContain("secret query");
    expect(json).not.toContain("private chain of thought");
    expect(json).not.toContain("TextBlock");
    expect(json).not.toContain("read_only");
  });
});
