import { describe, expect, it } from "vitest";

import type { Message } from "@/core/api/types";

import {
  assistantAnswerRequestsUserInput,
  extractContentFromMessage,
  extractPresentFilesFromMessage,
  extractTextFromMessage,
  groupMessages,
  hasContent,
  hasPresentFiles,
  hasReasoning,
  hasSubagent,
  hasToolCalls,
  isClarificationToolMessage,
  isLikelyFinalAnswerContent,
  isSettledAssistantAnswer,
  isAssistantStopTerminalState,
  latestAssistantTerminalState,
  parseUploadedFiles,
  stripInternalToolProtocol,
  sanitizeLegacyGuardDiagnostic,
  stripUploadedFilesTag,
} from "./utils";

describe("legacy guard diagnostics", () => {
  it("removes internal policy names and model repair instructions", () => {
    const cleaned = sanitizeLegacyGuardDiagnostic(
      "任务未能完成：始终无法满足「final-answer completeness guard」要求。最后一次拦截原因: The proposed Final Answer only announces a future inspection. Execute the stated read action.",
    );

    expect(cleaned).toContain("这轮任务没有完成");
    expect(cleaned).toContain("尚未形成可交付结果");
    expect(cleaned).not.toMatch(/guard|Final Answer|Execute the stated/i);
  });

  it("preserves ordinary source-code discussion", () => {
    const text = "请检查 final-answer completeness guard 的实现和测试。";
    expect(sanitizeLegacyGuardDiagnostic(text)).toBe(text);
  });

  it("repairs first-person explanations of internal guard failures", () => {
    const cleaned = sanitizeLegacyGuardDiagnostic(
      '我连续多次尝试给出最终答案，但系统反复拦截了我的回复，提示"final-answer completeness guard"未通过。',
    );

    expect(cleaned).toContain("尚未形成可交付结果");
    expect(cleaned).not.toContain("guard");
  });
});

describe("settled assistant answer detection", () => {
  const ai = (
    content: string,
    additional_kwargs: Record<string, unknown> = {},
  ): Message => ({
    id: "ai-answer",
    type: "ai",
    content,
    additional_kwargs,
  });

  it("accepts only ordinary settled answer prose", () => {
    expect(isSettledAssistantAnswer(ai("Final answer"))).toBe(true);
    expect(isSettledAssistantAnswer(ai("short"), { minTextLength: 6 })).toBe(
      false,
    );
  });

  it("rejects progress, commentary, interrupted, failed and streaming prose", () => {
    expect(
      isSettledAssistantAnswer(ai("progress", { public_progress: true })),
    ).toBe(false);
    expect(
      isSettledAssistantAnswer(
        ai("commentary", { message_kind: "commentary" }),
      ),
    ).toBe(false);
    expect(
      isSettledAssistantAnswer(
        ai("partial draft", { response_state: "interrupted" }),
      ),
    ).toBe(false);
    expect(
      isSettledAssistantAnswer(
        ai("failed draft", { response_state: "failed" }),
      ),
    ).toBe(false);
    expect(
      isSettledAssistantAnswer(
        ai("blocked draft", { response_state: "blocked" }),
      ),
    ).toBe(false);
    expect(
      isSettledAssistantAnswer(
        ai("still arriving", { run_status: "streaming" }),
      ),
    ).toBe(false);
  });

  it("does not treat a tool-call checkpoint as the final answer by default", () => {
    const toolMessage: Message = {
      id: "ai-tool",
      type: "ai",
      content: "I will inspect the file.",
      tool_calls: [{ id: "call-1", name: "read_file", args: {} }],
    };

    expect(isSettledAssistantAnswer(toolMessage)).toBe(false);
    expect(
      isSettledAssistantAnswer(toolMessage, { allowToolCalls: true }),
    ).toBe(true);
  });

  it("reports the latest explicit terminal state for the current turn", () => {
    expect(
      latestAssistantTerminalState([
        ai("older failure", { response_state: "failed" }),
        ai("progress", { public_progress: true }),
        ai("partial", { response_state: "interrupted" }),
      ]),
    ).toBe("interrupted");
    expect(latestAssistantTerminalState([ai("complete")])).toBeNull();
  });

  it("treats explicit cancellation and interruption as Stop, not failure", () => {
    expect(isAssistantStopTerminalState("cancelled")).toBe(true);
    expect(isAssistantStopTerminalState("interrupted")).toBe(true);
    expect(isAssistantStopTerminalState("failed")).toBe(false);
    expect(isAssistantStopTerminalState(null)).toBe(false);
  });

  it("treats blocked (needs user input) as a terminal state", () => {
    expect(
      latestAssistantTerminalState([
        ai("blocked handoff", { response_state: "blocked" }),
      ]),
    ).toBe("blocked");
    // A later blocked receipt supersedes an earlier failure receipt.
    expect(
      latestAssistantTerminalState([
        ai("older failure", { response_state: "failed" }),
        ai("blocked handoff", { response_state: "blocked" }),
      ]),
    ).toBe("blocked");
  });

  it("recovers blocked state from legacy final answers", () => {
    expect(
      assistantAnswerRequestsUserInput([
        ai("我已经检查完现有信息。请提供项目路径，我才能继续。"),
      ]),
    ).toBe(true);
    expect(
      assistantAnswerRequestsUserInput([
        ai("I need you to confirm which workspace should be changed."),
      ]),
    ).toBe(true);
    expect(
      assistantAnswerRequestsUserInput([
        ai("项目已经完成；如果需要，我可以继续补充测试。"),
      ]),
    ).toBe(false);
  });
});

describe("public assistant text sanitization", () => {
  it("removes leaked original-request prompt envelopes from assistant text", () => {
    const message = {
      type: "ai",
      content: [
        "处理中。",
        "[original-user-request]",
        "这是用户原始问题，不应在回答中重复。",
        "[/original-user-request]",
        "这是实际回答。",
      ].join("\n"),
    } as Message;

    expect(extractContentFromMessage(message)).toBe(
      "处理中。\n\n这是实际回答。",
    );
  });

  it("hides a truncated prompt envelope and its empty provider prefix", () => {
    const message = {
      type: "ai",
      content:
        "正在整理：[original-user-request]这是被截断的内部请求，尚未收到闭合标签",
    } as Message;

    expect(extractContentFromMessage(message)).toBe("");
  });

  it("keeps prompt-envelope syntax inside fenced code examples", () => {
    const message = {
      type: "ai",
      content:
        "示例：\n```text\n[original-user-request]\nhello\n[/original-user-request]\n```",
    } as Message;

    expect(extractContentFromMessage(message)).toContain(
      "[original-user-request]",
    );
  });

  it("removes internal context-recovery handoffs from the visible answer", () => {
    const text =
      "Resume state: inspected items.py and reducer.ts; continue from the next file.\n\nThe public conclusion follows.";
    expect(stripInternalToolProtocol(text)).toBe(
      "The public conclusion follows.",
    );
  });

  it("strips a batched tool-call json fence (array payload)", () => {
    // A model narrating its plan as a batched protocol array used to leak the
    // whole block into the chat copy: the fence matcher only covered
    // `{"command": <writer>}` objects, so `[{"tool": ...}, ...]` passed through.
    const text = [
      "开始只读审计：先摸清仓库结构。",
      "",
      "```json",
      "[",
      '  {"tool": "list_cwd", "args": {}},',
      '  {"tool": "git_status", "args": {}}',
      "]",
      "```",
    ].join("\n");
    const cleaned = stripInternalToolProtocol(text);
    expect(cleaned).toBe("开始只读审计：先摸清仓库结构。");
    expect(cleaned).not.toMatch(/"tool"\s*:/);
  });

  it("strips a single-object tool-call json fence keyed by tool", () => {
    const text = [
      "Checking the runtime entrypoint.",
      "",
      "```json",
      '{"tool": "read_file", "args": {"path": "Makefile"}}',
      "```",
    ].join("\n");
    expect(stripInternalToolProtocol(text)).toBe(
      "Checking the runtime entrypoint.",
    );
  });

  it("keeps a genuine json code fence that is not a tool call", () => {
    const text = [
      "Here is the config shape:",
      "",
      "```json",
      '{"name": "echo", "version": "0.2.0"}',
      "```",
    ].join("\n");
    const cleaned = stripInternalToolProtocol(text);
    expect(cleaned).toContain("```json");
    expect(cleaned).toContain('"version": "0.2.0"');
  });

  it("does not expose a recovery handoff when it is the entire answer", () => {
    expect(
      stripInternalToolProtocol(
        "Resume state: continue checking the remaining evidence.",
      ),
    ).toBe("");
  });

  it("removes a recovery handoff appended after a normal sentence", () => {
    expect(
      stripInternalToolProtocol(
        "任务已完成。 Resume state: 继续检查剩余证据。",
      ),
    ).toBe("任务已完成。");
  });

  it("removes a recovery handoff embedded in a process paragraph", () => {
    expect(
      stripInternalToolProtocol(
        "所有文件读取任务已完成，任务清单状态全部更新为已完成。Resume state: 本任务继续核对 reducer.ts。",
      ),
    ).toBe("所有文件读取任务已完成，任务清单状态全部更新为已完成。");
  });

  it("removes markdown-emphasized recovery handoffs", () => {
    expect(
      stripInternalToolProtocol(
        "所有文件读取任务已完成。 **Resume state:** 继续核对剩余证据。",
      ),
    ).toBe("所有文件读取任务已完成。");
  });

  it("removes unknown XML function envelopes before markdown rendering", () => {
    expect(
      stripInternalToolProtocol(
        "已完成。\n<function=custom_lookup><parameter=query>secret trace</parameter></function>",
      ),
    ).toBe("已完成。");
  });

  it("removes leaked seed tool-call markers from persisted replies", () => {
    expect(
      stripInternalToolProtocol(
        "我先检查文件。<seed:tool_call></seed:tool_call>",
      ),
    ).toBe("我先检查文件。");
  });

  it("removes bare search and browser tool calls from persisted replies", () => {
    expect(
      stripInternalToolProtocol(
        'web_search({"query":"Eight Sleep patent lawsuit"})',
      ),
    ).toBe("");
    expect(
      stripInternalToolProtocol('browser_open({"url":"https://example.com"})'),
    ).toBe("");
  });
});

describe("groupMessages", () => {
  it("keeps recovery-only truncated anchors out of the visible transcript", () => {
    const result = groupMessages(
      [
        { id: "1", type: "human", content: "继续调研" },
        {
          id: "2",
          type: "ai",
          content: "[上一轮任务进行到：]\n长内容…（后文已省略）",
          additional_kwargs: { hide_from_ui: true },
        },
      ] as Message[],
      (group) => group,
    );

    expect(result.map((group) => group.id)).toEqual(["1"]);
  });

  it("returns empty array for empty messages", () => {
    expect(groupMessages([], (group) => group)).toEqual([]);
  });

  it("groups human messages", () => {
    const result = groupMessages(
      [{ id: "1", type: "human", content: "Hello" } as Message],
      (group) => group,
    );

    expect(result).toHaveLength(1);
    expect(result[0]?.type).toBe("human");
    expect(result[0]?.messages).toHaveLength(1);
  });

  it("groups AI messages with content", () => {
    const result = groupMessages(
      [
        { id: "1", type: "human", content: "Hello" },
        { id: "2", type: "ai", content: "Hi there" },
      ] as Message[],
      (group) => group,
    );

    expect(result).toHaveLength(2);
    expect(result[1]?.type).toBe("assistant");
  });

  it("folds a duplicated legacy checkpoint into the following process group", () => {
    const checkpoint = "正在核对两个实现文件，随后给出结论。";
    const result = groupMessages(
      [
        { id: "1", type: "human", content: "检查实现" },
        { id: "2", type: "ai", content: checkpoint },
        {
          id: "3",
          type: "ai",
          content: "",
          additional_kwargs: { public_reasoning_summary: checkpoint },
          tool_calls: [
            { id: "read-1", name: "read_file", args: { path: "a.ts" } },
          ],
        },
      ] as Message[],
      (group) => group,
    );

    expect(result.map((group) => group.type)).toEqual([
      "human",
      "assistant:processing",
    ]);
    expect(result[1]?.messages.map((message) => message.id)).toEqual([
      "2",
      "3",
    ]);
  });

  it("groups AI messages with tool calls as processing", () => {
    const result = groupMessages(
      [
        { id: "1", type: "human", content: "Search for X" },
        {
          id: "2",
          type: "ai",
          content: "",
          tool_calls: [{ id: "tc1", name: "web_search", args: { query: "X" } }],
        },
      ] as Message[],
      (group) => group,
    );

    expect(result).toHaveLength(2);
    expect(result[1]?.type).toBe("assistant:processing");
  });

  it("also surfaces visible AI content when a tool-call message has text", () => {
    const result = groupMessages(
      [
        { id: "1", type: "human", content: "Search" },
        {
          id: "2",
          type: "ai",
          content: "Here is the summary.",
          tool_calls: [{ id: "tc1", name: "web_search", args: {} }],
        },
      ] as Message[],
      (group) => group,
    );

    expect(result.map((group) => group.type)).toEqual([
      "human",
      "assistant:processing",
    ]);
  });

  it("keeps reasoning emitted after an answer below that answer", () => {
    const result = groupMessages(
      [
        { id: "1", type: "human", content: "检查" },
        {
          id: "2",
          type: "ai",
          content: "先给出阶段性结论。",
        },
        {
          id: "3",
          type: "ai",
          content: "",
          additional_kwargs: {
            reasoning_content: "继续核对剩余证据。",
          },
        },
      ] as Message[],
      (group) => group,
    );

    expect(result.map((group) => group.type)).toEqual([
      "human",
      "assistant",
      "assistant:processing",
    ]);
    expect(result[1]?.messages.map((message) => message.id)).toEqual(["2"]);
    expect(result[2]?.messages.map((message) => message.id)).toEqual(["3"]);
  });

  it("keeps a final answer visible when a bookkeeping tool follows it", () => {
    const answer = {
      id: "answer-1",
      type: "ai",
      content: "项目分析完成。以下是完整分析报告。",
      additional_kwargs: { message_kind: "answer" },
    } as Message;
    const bookkeeping = {
      id: "todo-1",
      type: "ai",
      content: "",
      tool_calls: [
        {
          id: "todo-call-1",
          name: "todo_write",
          args: { todos: [{ content: "输出最终报告", status: "completed" }] },
        },
      ],
    } as Message;

    const result = groupMessages(
      [
        { id: "user-1", type: "human", content: "分析该项目" } as Message,
        answer,
        bookkeeping,
      ],
      (group) => group,
    );

    expect(result.map((group) => group.type)).toEqual([
      "human",
      "assistant",
      "assistant:processing",
    ]);
    expect(result[1]?.messages).toEqual([answer]);
    expect(result[2]?.messages).toEqual([bookkeeping]);
  });

  it("routes a short protocol answer beside its process from the first token", () => {
    const answer = {
      id: "2",
      type: "ai",
      content: "结论正在生成",
      additional_kwargs: { message_kind: "answer" },
      tool_calls: [{ id: "tc1", name: "read_file", args: {} }],
    } as Message;
    const result = groupMessages(
      [{ id: "1", type: "human", content: "检查" } as Message, answer],
      (group) => group,
    );

    expect(result.map((group) => group.type)).toEqual([
      "human",
      "assistant:processing",
      "assistant",
    ]);
    expect(result[1]?.messages).toContain(answer);
    expect(result[2]?.messages).toContain(answer);
  });

  it("never promotes protocol commentary by Markdown shape", () => {
    const commentary = {
      id: "2",
      type: "ai",
      content: "# 已完成调查\n\n这是很长的阶段性说明。".repeat(30),
      additional_kwargs: { message_kind: "commentary" },
    } as Message;

    expect(isLikelyFinalAnswerContent(commentary)).toBe(false);
  });

  it("groups tool messages with preceding processing group", () => {
    const result = groupMessages(
      [
        { id: "1", type: "human", content: "Search" },
        {
          id: "2",
          type: "ai",
          content: "",
          tool_calls: [{ id: "tc1", name: "web_search", args: {} }],
        },
        { id: "3", type: "tool", content: "results", tool_call_id: "tc1" },
      ] as Message[],
      (group) => group,
    );

    expect(result).toHaveLength(2);
    expect(result[1]?.type).toBe("assistant:processing");
    expect(result[1]?.messages).toHaveLength(2);
  });

  it("returns late tool results to the process group after interruption", () => {
    const result = groupMessages(
      [
        { id: "user-1", type: "human", content: "inspect" },
        {
          id: "ai-process",
          type: "ai",
          content: "",
          tool_calls: [
            { id: "read-1", name: "read_file", args: { path: "a.ts" } },
          ],
        },
        {
          id: "ai-interrupted",
          type: "ai",
          content: "",
          additional_kwargs: { response_state: "interrupted" },
        },
        {
          id: "tool-late",
          type: "tool",
          content: "late result",
          tool_call_id: "read-1",
        },
      ] as Message[],
      (group) => group,
    );

    expect(result.map((group) => group.type)).toEqual([
      "human",
      "assistant:processing",
      "assistant",
    ]);
    expect(result[1]?.messages.map((message) => message.id)).toEqual([
      "ai-process",
      "tool-late",
    ]);
    expect(result.at(-1)?.id).toBe("ai-interrupted");
  });

  it("keeps a blocked (needs user input) receipt as a terminal assistant group", () => {
    const result = groupMessages(
      [
        { id: "user-1", type: "human", content: "deploy" },
        {
          id: "ai-process",
          type: "ai",
          content: "",
          tool_calls: [
            { id: "shell-1", name: "shell", args: { command: "git push" } },
          ],
        },
        {
          id: "ai-blocked",
          type: "ai",
          content: "",
          additional_kwargs: {
            response_state: "blocked",
            error: {
              disposition: "blocked_on_user",
              failure_kind: "environment",
            },
          },
        },
      ] as Message[],
      (group) => group,
    );

    expect(result.map((group) => group.type)).toEqual([
      "human",
      "assistant:processing",
      "assistant",
    ]);
    expect(result.at(-1)?.id).toBe("ai-blocked");
  });

  it("folds late tool calls before an interrupted terminal receipt", () => {
    const result = groupMessages(
      [
        { id: "user-1", type: "human", content: "inspect" },
        {
          id: "ai-process",
          type: "ai",
          content: "",
          additional_kwargs: { public_progress: true },
        },
        {
          id: "ai-interrupted",
          type: "ai",
          content: "",
          additional_kwargs: { response_state: "interrupted" },
        },
        {
          id: "ai-late-call",
          type: "ai",
          content: "",
          tool_calls: [
            { id: "shell-1", name: "shell", args: { command: "pwd" } },
          ],
        },
        {
          id: "tool-late",
          type: "tool",
          content: "/workspace",
          tool_call_id: "shell-1",
        },
      ] as Message[],
      (group) => group,
    );

    expect(result.map((group) => group.type)).toEqual([
      "human",
      "assistant:processing",
      "assistant",
    ]);
    expect(result[1]?.messages.map((message) => message.id)).toEqual([
      "ai-process",
      "ai-late-call",
      "tool-late",
    ]);
    expect(result.at(-1)?.id).toBe("ai-interrupted");
  });

  it("groups present_files as a separate group", () => {
    const result = groupMessages(
      [
        { id: "1", type: "human", content: "Show files" },
        {
          id: "2",
          type: "ai",
          content: "",
          tool_calls: [
            {
              id: "tc1",
              name: "present_files",
              args: { filepaths: ["/a.txt"] },
            },
          ],
        },
        {
          id: "3",
          type: "tool",
          content: "files presented",
          tool_call_id: "tc1",
        },
      ] as Message[],
      (group) => group,
    );

    expect(result).toHaveLength(2);
    expect(result[1]?.type).toBe("assistant:present-files");
    expect(result[1]?.messages.map((message) => message.id)).toEqual([
      "2",
      "3",
    ]);
  });

  it("indexes future transcript signals without slicing the remaining array", () => {
    const messages = [
      { id: "1", type: "human", content: "Investigate" },
      { id: "2", type: "ai", content: "I will inspect it." },
      {
        id: "3",
        type: "ai",
        content: "",
        tool_calls: [{ id: "read-1", name: "read_file", args: {} }],
      },
      { id: "4", type: "tool", content: "done", tool_call_id: "read-1" },
    ] as Message[];
    Object.defineProperty(messages, "slice", {
      configurable: true,
      value: () => {
        throw new Error("groupMessages must not copy the remaining transcript");
      },
    });

    expect(
      groupMessages(messages, (group) => group).map((group) => group.type),
    ).toEqual(["human", "assistant:processing"]);
  });

  it("hides superseded approval timeout errors once a later answer exists", () => {
    const result = groupMessages(
      [
        { id: "1", type: "human", content: "做一个nas调研" },
        {
          id: "2",
          type: "ai",
          content:
            "出错了：timed out waiting for item/commandExecution/requestApproval",
          additional_kwargs: {
            error: {
              message:
                "timed out waiting for item/commandExecution/requestApproval",
            },
          },
        },
        {
          id: "3",
          type: "ai",
          content: "消费级 NAS 市场调研\n\n执行摘要...",
        },
      ] as Message[],
      (group) => group,
    );

    expect(result.map((group) => group.id)).toEqual(["1", "3"]);
    expect(JSON.stringify(result)).not.toContain("timed out waiting");
  });

  it("groups subagent task calls", () => {
    const result = groupMessages(
      [
        { id: "1", type: "human", content: "Do task" },
        {
          id: "2",
          type: "ai",
          content: "",
          tool_calls: [
            {
              id: "tc1",
              name: "task",
              args: {
                subagent_type: "researcher",
                description: "Research",
                prompt: "Go",
              },
            },
          ],
        },
      ] as Message[],
      (group) => group,
    );

    expect(result).toHaveLength(2);
    expect(result[1]?.type).toBe("assistant:subagent");
  });

  it("skips todo_reminder messages", () => {
    const result = groupMessages(
      [
        {
          id: "1",
          type: "human",
          content: "Hello",
          name: "todo_reminder",
        } as Message,
      ],
      (group) => group,
    );

    expect(result).toHaveLength(0);
  });

  it("skips hide_from_ui messages", () => {
    const result = groupMessages(
      [
        {
          id: "1",
          type: "human",
          content: "Hidden",
          additional_kwargs: { hide_from_ui: true },
        } as Message,
      ],
      (group) => group,
    );

    expect(result).toHaveLength(0);
  });

  it("skips empty planner no-output placeholders", () => {
    const result = groupMessages(
      [
        { id: "1", type: "human", content: "Plan something" },
        { id: "2", type: "ai", content: "[planner] (no output)" },
      ] as Message[],
      (group) => group,
    );

    expect(result).toHaveLength(1);
    expect(result[0]?.type).toBe("human");
  });
});

describe("message content helpers", () => {
  it("extracts text from string content", () => {
    expect(
      extractTextFromMessage({
        type: "human",
        content: "Hello world",
      } as Message),
    ).toBe("Hello world");
  });

  it("extracts text from array content", () => {
    const message = {
      type: "human",
      content: [
        { type: "text", text: "Hello " },
        { type: "text", text: "world" },
      ],
    } as Message;

    expect(extractTextFromMessage(message)).toBe("Hello\nworld");
  });

  it("strips think tags from AI text content", () => {
    const result = extractTextFromMessage({
      type: "ai",
      content: "<think>reasoning</think> Actual answer",
    } as Message);

    expect(result).not.toContain("<think");
    expect(result).toContain("Actual answer");
  });

  it("hides leaked internal tool fences from AI text content", () => {
    const result = extractContentFromMessage({
      type: "ai",
      content: 'I\'ll edit it now.\n\n```tool\nfs_writer\n{"path":"/tmp/a.ts"}',
    } as Message);

    expect(result).toBe("I'll edit it now.");
  });

  it("treats bare leaked pseudo-tool payloads as no visible AI content", () => {
    const message = {
      type: "ai",
      content: 'fs_writen\n{"path":"/tmp/a.ts","content":"x"}',
    } as Message;

    expect(extractTextFromMessage(message)).toBe("");
    expect(hasContent(message)).toBe(false);
  });

  it("treats leaked JSON command tool payloads as no visible AI content", () => {
    const message = {
      type: "ai",
      content:
        '{"command":"write_file","kwargs":{"path":"C:\\\\Users\\\\Bryce\\\\.echo\\\\workspace\\\\plan.md","content":"# Plan"}}',
    } as Message;

    expect(extractTextFromMessage(message)).toBe("");
    expect(hasContent(message)).toBe(false);
  });

  it("treats leaked JSON command payloads in array content as no visible AI content", () => {
    const message = {
      type: "ai",
      content: [
        {
          type: "text",
          text: '{"command":"write_file","kwargs":{"path":"C:\\\\Users\\\\Bryce\\\\.echo\\\\workspace\\\\plan.md","content":"# Plan"}}',
        },
      ],
    } as Message;

    expect(extractTextFromMessage(message)).toBe("");
    expect(hasContent(message)).toBe(false);
  });

  it("treats role no-output placeholders as no visible AI content", () => {
    const message = {
      type: "ai",
      content: "[planner] (no output)",
    } as Message;

    expect(extractTextFromMessage(message)).toBe("");
    expect(hasContent(message)).toBe(false);
  });

  it("strips leaked team-role prefixes and start markers from AI text content", () => {
    const startMessage = {
      type: "ai",
      content: "[planner] starting · agent=planner\n继续执行中……",
    } as Message;
    const summaryMessage = {
      type: "ai",
      content: "[critic] 1. Consensus\nStatus: No majority",
    } as Message;

    expect(extractTextFromMessage(startMessage)).toBe("继续执行中……");
    expect(extractTextFromMessage(summaryMessage)).toBe(
      "1. Consensus\nStatus: No majority",
    );
  });

  it("treats repeated null placeholders as no visible AI content", () => {
    const message = {
      type: "ai",
      content: "nullnullnull",
    } as Message;

    expect(extractTextFromMessage(message)).toBe("");
    expect(hasContent(message)).toBe(false);
  });

  it("treats leaked read-only control tags as no visible AI content", () => {
    const message = {
      type: "ai",
      content: "<read_only> </read_only>",
    } as Message;

    expect(extractTextFromMessage(message)).toBe("");
    expect(hasContent(message)).toBe(false);
  });

  it("strips inline read-only control tags before grouping visibility checks", () => {
    const message = {
      type: "ai",
      content: "我先核对实现。<read_only> </read_only> 现在信息已经足够。",
    } as Message;

    expect(extractTextFromMessage(message)).toBe(
      "我先核对实现。 现在信息已经足够。",
    );
    expect(hasContent(message)).toBe(true);
  });

  it("strips leaked renderer component tags before grouping visibility checks", () => {
    const message = {
      type: "ai",
      content: "摘要应显示为 `<TextBlock>`，不是阶段分析。",
    } as Message;

    expect(extractTextFromMessage(message)).toBe(
      "摘要应显示为，不是阶段分析。",
    );
    expect(hasContent(message)).toBe(true);
  });

  it("keeps internal renderer tags in fenced code examples", () => {
    const message = {
      type: "ai",
      content: "```tsx\n<TextBlock>hello</TextBlock>\n```",
    } as Message;

    expect(extractTextFromMessage(message)).toContain("<TextBlock>");
    expect(hasContent(message)).toBe(true);
  });

  it("strips leaked task JSON command fences but keeps surrounding prose", () => {
    const message = {
      type: "ai",
      content:
        '抱歉，回复中断了。我现在开始制定调研计划并创建文档。\n\n**Task: Create the research plan document**\n```json\n{"command":"write_file","kwargs":{"path":"C:\\\\Users\\\\Bryce\\\\.echo\\\\workspace\\\\plan.md","content":"# Plan"}}\n```',
    } as Message;

    expect(extractTextFromMessage(message)).toBe(
      "抱歉，回复中断了。我现在开始制定调研计划并创建文档。",
    );
    expect(extractTextFromMessage(message)).not.toContain("write_file");
    expect(extractTextFromMessage(message)).not.toContain("Bryce");
  });

  it("strips leaked XML tool calls but keeps surrounding prose", () => {
    const message = {
      type: "ai",
      content:
        "抱歉反复中断。我直接把完整计划写出来：<tool_call>\n<function=write_file>\n<path>C:\\Users\\Bryce\\.echo\\workspace\\plan.md</path>\n<content># Plan</content>\n</function>\n</tool_call>",
    } as Message;

    expect(extractTextFromMessage(message)).toBe(
      "抱歉反复中断。我直接把完整计划写出来：",
    );
    expect(extractTextFromMessage(message)).not.toContain("write_file");
    expect(extractTextFromMessage(message)).not.toContain("Bryce");
  });

  it("strips leaked self-closing tool invocation tags from persisted AI messages", () => {
    const message = {
      type: "ai",
      content:
        'I will check whether the file exists.\n\n<tool_invocation name="list_cwd" arguments={} />',
    } as Message;

    expect(extractTextFromMessage(message)).toBe(
      "I will check whether the file exists.",
    );
    expect(extractTextFromMessage(message)).not.toContain("tool_invocation");
    expect(extractTextFromMessage(message)).not.toContain("list_cwd");
  });

  it("strips leaked ReAct protocol actions and keeps the final answer", () => {
    const message = {
      type: "ai",
      content:
        'Thought: 我需要创建文件。\n\nAction: write_file({"path":"C:\\\\Users\\\\Bryce\\\\.echo\\\\workspace\\\\plan.md","content":"# Plan"})\n\nFinal Answer:\n\n计划已经准备好了。',
    } as Message;

    expect(extractTextFromMessage(message)).toBe("计划已经准备好了。");
    expect(extractTextFromMessage(message)).not.toContain("write_file");
    expect(extractTextFromMessage(message)).not.toContain("Bryce");
  });

  it("strips leaked ReAct observations and redacts secret assignments", () => {
    const observationMessage = {
      type: "ai",
      content:
        "我先核对环境。\nObservation: token=super-secret api_key=sk-test-123\nFinal Answer: 环境检查完成。",
    } as Message;

    expect(extractTextFromMessage(observationMessage)).toBe("环境检查完成。");
    expect(extractTextFromMessage(observationMessage)).not.toContain(
      "super-secret",
    );
    expect(extractTextFromMessage(observationMessage)).not.toContain("sk-test");

    const proseMessage = {
      type: "ai",
      content: "配置已读取，token=super-secret 将不会展示。",
    } as Message;

    expect(extractTextFromMessage(proseMessage)).toBe(
      "配置已读取，token=[redacted] 将不会展示。",
    );
  });

  it("extracts image URL content as markdown", () => {
    const result = extractContentFromMessage({
      type: "human",
      content: [
        { type: "image_url", image_url: { url: "https://example.test/a.png" } },
      ],
    } as Message);

    expect(result).toBe("![image](https://example.test/a.png)");
  });
});

describe("message predicates", () => {
  it("detects content presence", () => {
    expect(hasContent({ type: "human", content: "Hello" } as Message)).toBe(
      true,
    );
    expect(hasContent({ type: "human", content: "" } as Message)).toBe(false);
    expect(
      hasContent({
        type: "human",
        content: [{ type: "text", text: "Hi" }],
      } as Message),
    ).toBe(true);
  });

  it("detects reasoning content", () => {
    expect(
      hasReasoning({
        type: "ai",
        content: "",
        additional_kwargs: { reasoning_content: "thinking..." },
      } as Message),
    ).toBe(true);
    expect(hasReasoning({ type: "human", content: "Hello" } as Message)).toBe(
      false,
    );
    expect(
      hasReasoning({
        type: "ai",
        content: "<think>reasoning</think> Answer",
      } as Message),
    ).toBe(true);
  });

  it("detects tool calls and special tool-call categories", () => {
    expect(
      hasToolCalls({
        type: "ai",
        content: "",
        tool_calls: [{ id: "1", name: "search", args: {} }],
      } as Message),
    ).toBe(true);
    expect(hasToolCalls({ type: "ai", content: "Hello" } as Message)).toBe(
      false,
    );
    expect(
      hasPresentFiles({
        type: "ai",
        content: "",
        tool_calls: [{ id: "1", name: "present_files", args: {} }],
      } as Message),
    ).toBe(true);
    expect(
      hasSubagent({
        type: "ai",
        content: "",
        tool_calls: [{ id: "1", name: "task", args: {} }],
      } as Message),
    ).toBe(true);
  });

  it("detects clarification tool messages", () => {
    expect(
      isClarificationToolMessage({
        type: "tool",
        name: "ask_clarification",
      } as Message),
    ).toBe(true);
    expect(
      isClarificationToolMessage({
        type: "tool",
        name: "ask_user_question",
      } as Message),
    ).toBe(true);
    expect(
      isClarificationToolMessage({ type: "tool", name: "search" } as Message),
    ).toBe(false);
  });
});

describe("isLikelyFinalAnswerContent", () => {
  it("returns false for empty content", () => {
    expect(
      isLikelyFinalAnswerContent({ type: "ai", content: "" } as Message),
    ).toBe(false);
  });

  it("returns true for content longer than 320 chars", () => {
    const longText = "a".repeat(321);
    expect(
      isLikelyFinalAnswerContent({ type: "ai", content: longText } as Message),
    ).toBe(true);
  });

  it("returns true for content with markdown heading", () => {
    expect(
      isLikelyFinalAnswerContent({
        type: "ai",
        content: "# Summary\n\nSome text",
      } as Message),
    ).toBe(true);
    expect(
      isLikelyFinalAnswerContent({
        type: "ai",
        content: "## Section\n\nMore text",
      } as Message),
    ).toBe(true);
    expect(
      isLikelyFinalAnswerContent({
        type: "ai",
        content: "### Subsection\n\nDetails",
      } as Message),
    ).toBe(true);
  });

  it("returns false for short plain text", () => {
    expect(
      isLikelyFinalAnswerContent({ type: "ai", content: "Hello" } as Message),
    ).toBe(false);
  });

  it("returns true for Chinese numbered lists", () => {
    expect(
      isLikelyFinalAnswerContent({
        type: "ai",
        content: "一、第一项\n二、第二项\n三、第三项\n四、第四项",
      } as Message),
    ).toBe(true);
  });

  it("returns true for numbered lists with 4+ lines", () => {
    expect(
      isLikelyFinalAnswerContent({
        type: "ai",
        content: "1. First\n2. Second\n3. Third\n4. Fourth",
      } as Message),
    ).toBe(true);
  });

  it("returns false for numbered lists with fewer than 4 lines", () => {
    expect(
      isLikelyFinalAnswerContent({
        type: "ai",
        content: "1. First\n2. Second\n3. Third",
      } as Message),
    ).toBe(false);
  });

  it("returns true for markdown tables", () => {
    expect(
      isLikelyFinalAnswerContent({
        type: "ai",
        content: "| Name | Value |\n|------|-------|\n| A    | 1     |",
      } as Message),
    ).toBe(true);
  });

  it("returns false for pipe characters without table separator", () => {
    expect(
      isLikelyFinalAnswerContent({
        type: "ai",
        content: "a | b | c",
      } as Message),
    ).toBe(false);
  });
});

describe("uploaded and presented files", () => {
  it("strips uploaded_files tags", () => {
    const content =
      "Some text <uploaded_files>- file.txt (1KB)\n  Path: /a</uploaded_files> more text";

    expect(stripUploadedFilesTag(content)).toBe("Some text  more text");
  });

  it("returns content unchanged when no uploaded_files tag exists", () => {
    expect(stripUploadedFilesTag("Just regular text")).toBe(
      "Just regular text",
    );
  });

  it("parses file entries from uploaded_files tags", () => {
    const files = parseUploadedFiles(`<uploaded_files>- report.pdf (1024)
  Path: /data/report.pdf</uploaded_files>`);

    expect(files).toEqual([
      {
        filename: "report.pdf",
        size: 1024,
        path: "/data/report.pdf",
      },
    ]);
  });

  it("returns empty for no uploaded files", () => {
    expect(parseUploadedFiles("No files here")).toEqual([]);
    expect(
      parseUploadedFiles(
        "<uploaded_files>No files have been uploaded yet.</uploaded_files>",
      ),
    ).toEqual([]);
  });

  it("extracts filepaths from present_files tool calls", () => {
    const files = extractPresentFilesFromMessage({
      type: "ai",
      content: "",
      tool_calls: [
        {
          id: "1",
          name: "present_files",
          args: { filepaths: ["/a.txt", "/b.txt"] },
        },
      ],
    } as Message);

    expect(files).toEqual(["/a.txt", "/b.txt"]);
  });

  it("returns empty for non-present_files messages", () => {
    const files = extractPresentFilesFromMessage({
      type: "ai",
      content: "",
      tool_calls: [{ id: "1", name: "search", args: {} }],
    } as Message);

    expect(files).toEqual([]);
  });
});
