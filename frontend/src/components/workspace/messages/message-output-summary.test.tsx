import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AIMessage, Message, ToolMessage } from "@/core/api/types";
import type { BaseStream } from "@/core/api/use-stream-types";
import type { AgentThreadState } from "@/core/threads";
import { SubtasksProvider } from "@/core/tasks/context";
import { renderWithProviders } from "@/test/harness";

import { AGENT_WORKBENCH_OPEN_EVENT } from "../agent-workbench-events";
import { ThreadProviders } from "./context";
import { MessageList } from "./message-list";
import {
  extractResultUrl,
  MessageOutputSummary,
} from "./message-output-summary";

const selectArtifact = vi.fn();
const setArtifactsOpen = vi.fn();

vi.mock("../artifacts", () => ({
  useArtifacts: () => ({
    setOpen: setArtifactsOpen,
    autoOpen: false,
    autoSelect: false,
    selectedArtifact: null,
    select: selectArtifact,
  }),
}));

vi.mock("@/core/settings", () => ({
  useLocalSettings: () => [
    {
      display: {
        chat_font_size: "medium",
      },
    },
    vi.fn(),
  ],
}));

function mockThread(
  overrides: Partial<BaseStream<AgentThreadState>> = {},
): BaseStream<AgentThreadState> {
  const messages = overrides.messages ?? [];
  return {
    messages,
    streamingMessage: null,
    subgraphStreams: {},
    values: {
      title: "",
      messages,
      artifacts: [],
    },
    isLoading: false,
    isThreadLoading: false,
    error: undefined,
    stop: vi.fn(),
    refresh: vi.fn(),
    submit: vi.fn(),
    threadId: "thread-1",
    ...overrides,
  };
}

function renderMessageList(
  thread: BaseStream<AgentThreadState>,
  locale: "en-US" | "zh-CN" = "zh-CN",
) {
  return renderWithProviders(
    <SubtasksProvider>
      <ThreadProviders thread={thread}>
        <MessageList
          threadId="thread-1"
          thread={thread}
          paddingBottom={0}
          mode="chat"
        />
      </ThreadProviders>
    </SubtasksProvider>,
    { locale, initialRoute: "/workspace/realtime/thread-1" },
  );
}

/** Realistic turn: human prompt → tool-call AI (verification) → tool
 *  result → plain assistant final answer. */
function verificationTurnMessages(): {
  human: Message;
  verificationAi: AIMessage;
  verificationResult: ToolMessage;
  finalAnswer: Message;
} {
  const human: Message = {
    id: "user-1",
    type: "human",
    content: [
      "修复登录页的报错",
      "<uploaded_files>",
      "- notes.txt (1 KB)",
      "  Path: /tmp/notes.txt",
      "</uploaded_files>",
    ].join("\n"),
  };
  const verificationAi: AIMessage = {
    id: "ai-tools",
    type: "ai",
    content: "",
    tool_calls: [{ id: "verify-1", name: "run_tests", args: {} }],
  };
  const verificationResult: ToolMessage = {
    id: "tool-verify-1",
    type: "tool",
    content: '{"success": true, "stdout": "12 passed"}',
    tool_call_id: "verify-1",
  };
  const finalAnswer: Message = {
    id: "ai-final",
    type: "ai",
    content: "已修复登录页报错，测试全部通过。",
  };
  return { human, verificationAi, verificationResult, finalAnswer };
}

describe("MessageOutputSummary", () => {
  beforeEach(() => {
    selectArtifact.mockClear();
    setArtifactsOpen.mockClear();
    vi.unstubAllGlobals();
  });

  it("renders generated artifacts and opens the artifact panel", () => {
    const message: AIMessage = {
      id: "ai-1",
      type: "ai",
      content: "Done",
      tool_calls: [
        {
          id: "artifact-1",
          name: "artifact",
          args: {
            path: "reports/README.md",
            title: "README.md",
            kind: "Markdown",
          },
        },
      ],
    };

    renderWithProviders(<MessageOutputSummary messages={[message]} />, {
      locale: "zh-CN",
    });

    fireEvent.click(screen.getByText("README.md"));

    expect(screen.getByText("产物汇总")).toBeInTheDocument();
    expect(screen.getByText("Markdown")).toBeInTheDocument();
    expect(selectArtifact).toHaveBeenCalledWith("reports/README.md");
    expect(setArtifactsOpen).toHaveBeenCalledWith(true);
  });

  it("opens an absolute workspace report through the scoped preview endpoint", () => {
    const message: AIMessage = {
      id: "ai-1",
      type: "ai",
      content: "Done",
      tool_calls: [
        {
          id: "artifact-1",
          name: "artifact",
          args: {
            path: "/tmp/data/workspaces/thread-1/output/final/nas-report.md",
            title: "nas-report.md",
          },
        },
      ],
    };

    renderWithProviders(
      <MessageOutputSummary messages={[message]} threadId="thread-1" />,
      { locale: "zh-CN" },
    );

    fireEvent.click(screen.getByText("nas-report.md"));

    expect(selectArtifact).toHaveBeenCalledWith(
      "workspace-output:final:nas-report.md",
    );
  });

  it("delegates artifact navigation to the host workbench when provided", () => {
    const onOpenArtifact = vi.fn();
    const message: AIMessage = {
      id: "ai-workbench-artifact",
      type: "ai",
      content: "Done",
      tool_calls: [
        {
          id: "artifact-workbench-1",
          name: "artifact",
          args: { path: "output/final/report.md", title: "report.md" },
        },
      ],
    };

    renderWithProviders(
      <MessageOutputSummary
        messages={[message]}
        threadId="thread-1"
        onOpenArtifact={onOpenArtifact}
      />,
      { locale: "zh-CN" },
    );

    fireEvent.click(screen.getByText("report.md"));

    expect(onOpenArtifact).toHaveBeenCalledWith("output/final/report.md");
    expect(setArtifactsOpen).not.toHaveBeenCalled();
  });

  it("delegates failed-task retry so the host can preserve project context", () => {
    const onRetryTask = vi.fn();
    const human: Message = {
      id: "user-retry",
      type: "human",
      content: "分析该项目",
    };

    renderWithProviders(
      <MessageOutputSummary
        messages={[human]}
        failure={{
          message: "任务失败",
          detail: "redaction changed journal ownership scope",
          kind: "error",
        }}
        onRetryTask={onRetryTask}
      />,
      { locale: "zh-CN" },
    );

    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    expect(onRetryTask).toHaveBeenCalledWith("分析该项目");
    expect(window.location.hash).toBe("");
  });

  it("retries the original objective when the failed turn is punctuation-only", () => {
    const onRetryTask = vi.fn();
    const original: Message = {
      id: "user-original",
      type: "human",
      content: "调研 Eight Sleep 的专利诉讼",
    };
    const nudge: Message = {
      id: "user-nudge",
      type: "human",
      content: "？？",
    };

    renderWithProviders(
      <MessageOutputSummary
        messages={[]}
        turnMessages={[nudge]}
        retryContextMessages={[original, nudge]}
        failure={{ message: "任务中断", kind: "error" }}
        onRetryTask={onRetryTask}
      />,
      { locale: "zh-CN" },
    );

    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    expect(onRetryTask).toHaveBeenCalledWith("调研 Eight Sleep 的专利诉讼");
  });

  it("renders file changes as a reviewable completed change set", () => {
    const message: AIMessage = {
      id: "ai-1",
      type: "ai",
      content: "Done",
      tool_calls: [
        {
          id: "artifact-1",
          name: "artifact",
          args: {
            path: "workspace-output:final:nas_market_research_plan.md",
            title: "nas_market_research_plan.md",
            kind: "Markdown",
          },
        },
        {
          id: "change-1",
          name: "file_change",
          args: {
            changes: [
              {
                path: "runtime/safety/regeneration/native_llm_replay.py",
                op: "update",
                diff: [
                  "--- a/runtime/safety/regeneration/native_llm_replay.py",
                  "+++ b/runtime/safety/regeneration/native_llm_replay.py",
                  "@@",
                  "-old",
                  "+new",
                  "+another",
                ].join("\n"),
              },
            ],
          },
        },
      ],
    };

    renderWithProviders(<MessageOutputSummary messages={[message]} />, {
      locale: "zh-CN",
    });

    const opened: CustomEvent[] = [];
    const handleOpen = (event: Event) => opened.push(event as CustomEvent);
    window.addEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);

    expect(screen.getByText("已完成改动")).toBeInTheDocument();
    expect(screen.getByText("已编辑 1 个文件")).toBeInTheDocument();
    expect(screen.queryByText("任务产物")).not.toBeInTheDocument();
    const changeSet = screen.getByRole("region", {
      name: "文件变更汇总",
    });
    expect(changeSet).toHaveAttribute("data-testid", "output-change-set");
    expect(changeSet.className).toMatch(/\brounded-xl\b/);
    expect(changeSet.className).toMatch(/\bborder\b/);
    expect(
      screen.getByText("runtime/safety/regeneration/native_llm_replay.py"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("+2 -1")).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "文件变更汇总" })[0]);

    expect(opened.at(-1)?.detail).toEqual({ tab: "diff" });
    window.removeEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
  });

  it("puts the hunk disclosure at the end of the file row", () => {
    const message: AIMessage = {
      id: "ai-hunks",
      type: "ai",
      content: "Done",
      tool_calls: [
        {
          id: "change-hunks",
          name: "file_change",
          args: {
            changes: [
              {
                path: "runtime/app.py",
                op: "update",
                diff: ["@@", "-old", "+new"].join("\n"),
                hunks: [
                  {
                    id: "hunk-1",
                    oldStart: 1,
                    oldLines: 1,
                    newStart: 1,
                    newLines: 1,
                    body: "-old\n+new",
                  },
                ],
              },
            ],
          },
        },
      ],
    };

    renderWithProviders(<MessageOutputSummary messages={[message]} />, {
      locale: "zh-CN",
    });

    // 展开箭头在行尾，不在文件名前面：文件名才是扫读目标。
    const path = screen.getByText("runtime/app.py");
    const toggle = screen
      .getByRole("region", { name: "文件变更汇总" })
      .querySelector("button[aria-expanded]");
    expect(toggle).not.toBeNull();
    expect(
      path.compareDocumentPosition(toggle!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("labels newly created files as generated artifacts", () => {
    const message: AIMessage = {
      id: "ai-1",
      type: "ai",
      content: "Done",
      tool_calls: [
        {
          id: "change-1",
          name: "file_change",
          args: {
            changes: [
              {
                path: "data/workspaces/thread-1/output/final/nas_market_research_plan.md",
                op: "create",
                diff: [
                  "--- /dev/null",
                  "+++ b/data/workspaces/thread-1/output/final/nas_market_research_plan.md",
                  "@@ -0,0 +1,2 @@",
                  "+# NAS市场调研计划",
                  "+正文",
                ].join("\n"),
              },
            ],
          },
        },
      ],
    };

    renderWithProviders(
      <MessageOutputSummary messages={[message]} threadId="thread-1" />,
      { locale: "zh-CN" },
    );

    expect(
      screen.queryByText(/任务完成 · 已生成 1 个产物/),
    ).not.toBeInTheDocument();
    expect(screen.getByText("已完成改动")).toBeInTheDocument();
    expect(screen.getByText("已生成 1 个产物")).toBeInTheDocument();
    expect(screen.queryByText("已编辑 1 个文件")).not.toBeInTheDocument();
    expect(screen.queryByText("新建")).not.toBeInTheDocument();
    expect(
      screen.queryByText(
        "data/workspaces/thread-1/output/final/nas_market_research_plan.md",
      ),
    ).not.toBeInTheDocument();
    expect(screen.getAllByText("nas_market_research_plan.md")).toHaveLength(1);
    expect(
      screen.queryByRole("button", { name: "接受" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /撤销/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("审核交给")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("nas_market_research_plan.md"));

    expect(selectArtifact).toHaveBeenCalledWith(
      "workspace-output:final:nas_market_research_plan.md",
    );
    expect(setArtifactsOpen).toHaveBeenCalledWith(true);
    expect(
      screen.queryByRole("button", { name: "查看过程" }),
    ).not.toBeInTheDocument();
  });

  it("keeps review controls out of the completed change set", () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ success: true }),
      statusText: "OK",
    });
    vi.stubGlobal("fetch", fetchMock);
    const message: AIMessage = {
      id: "ai-1",
      type: "ai",
      content: "Done",
      tool_calls: [
        {
          id: "change-1",
          name: "file_change",
          args: {
            changes: [
              {
                path: "runtime/generated/report.md",
                op: "create",
                diff: [
                  "--- /dev/null",
                  "+++ b/runtime/generated/report.md",
                  "@@ -0,0 +1 @@",
                  "+# Report",
                ].join("\n"),
              },
            ],
          },
        },
      ],
    };

    renderWithProviders(
      <MessageOutputSummary
        auditNotice="需要先审核这次产物变更"
        messages={[message]}
        threadId="thread-1"
      />,
      { locale: "zh-CN" },
    );

    expect(
      screen.queryByText("需要先审核这次产物变更"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("任务产物")).not.toBeInTheDocument();
    expect(screen.getByText("已完成改动")).toBeInTheDocument();
    expect(screen.getByText("已编辑 1 个文件")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /撤销/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("审核交给")).not.toBeInTheDocument();
    expect(screen.getByText("runtime/generated/report.md")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("scans the turn slice for verifications without a duplicate task action", () => {
    const { human, verificationAi, verificationResult, finalAnswer } =
      verificationTurnMessages();

    renderWithProviders(
      <MessageOutputSummary
        messages={[finalAnswer]}
        turnMessages={[human, verificationAi, verificationResult, finalAnswer]}
      />,
      { locale: "zh-CN" },
    );

    expect(screen.getByText("验证")).toBeInTheDocument();
    expect(screen.getByText("测试通过")).toBeInTheDocument();
    expect(screen.getByText("通过")).toBeInTheDocument();
    expect(screen.getByText(/任务完成.*验证 1\/1/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /做同款/ }),
    ).not.toBeInTheDocument();
  });
});

describe("extractResultUrl", () => {
  it("matches Cloudflare Pages deploy URLs on *.pages.dev", () => {
    const message: AIMessage = {
      id: "ai-1",
      type: "ai",
      content: "Deployed to https://myapp.pages.dev/ for preview.",
    };
    expect(extractResultUrl([message])).toBe("https://myapp.pages.dev/");
  });

  it("ignores unrelated hosts", () => {
    const message: AIMessage = {
      id: "ai-1",
      type: "ai",
      content: "See https://example.com/docs for details.",
    };
    expect(extractResultUrl([message])).toBeNull();
  });
});

describe("MessageList receipt wiring", () => {
  it("renders the verification list without a make-similar action", () => {
    const { human, verificationAi, verificationResult, finalAnswer } =
      verificationTurnMessages();
    const thread = mockThread({
      messages: [human, verificationAi, verificationResult, finalAnswer],
    });

    renderMessageList(thread);

    expect(screen.getByText("测试通过")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /做同款/ }),
    ).not.toBeInTheDocument();
  });

  it("keeps unknown verification tool names and protocol details out of receipts", () => {
    const human: Message = {
      id: "user-1",
      type: "human",
      content: "验证一下",
    };
    const verificationAi: AIMessage = {
      id: "ai-tools",
      type: "ai",
      content: "",
      tool_calls: [{ id: "verify-1", name: "verify_contract_case", args: {} }],
    };
    const verificationResult: ToolMessage = {
      id: "tool-verify-1",
      type: "tool",
      content: JSON.stringify({
        success: false,
        error:
          "Action: read_file\n失败原因：token=super-secret\nObservation: exec_shell returned 1",
      }),
      tool_call_id: "verify-1",
      status: "error",
    };
    renderWithProviders(
      <MessageOutputSummary
        messages={[human, verificationAi, verificationResult]}
      />,
      { locale: "zh-CN" },
    );

    expect(screen.getAllByText("验证").length).toBeGreaterThan(0);
    expect(screen.queryByText("verify_contract_case")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/read_file|Observation|super-secret/i),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/失败原因：token=«redacted»/)).toBeInTheDocument();
    expect(screen.queryByText(/operation returned 1/i)).not.toBeInTheDocument();
  });
});

describe("MessageList failure visibility", () => {
  it("does not duplicate a visible empty-output assistant error with a failure receipt", () => {
    const messages: Message[] = [
      { id: "user-1", type: "human", content: "你好" },
      {
        id: "assistant-1",
        type: "ai",
        content:
          "出错了：模型执行结束但没有返回任何可见输出。请重试，或切换到其他可用模型后再试。",
      },
    ];
    const thread = mockThread({
      messages,
      error: new Error(
        "模型执行结束但没有返回任何可见输出。请重试，或切换到其他可用模型后再试。",
      ),
    });

    renderMessageList(thread);

    expect(
      screen.getByText(/模型执行结束但没有返回任何可见输出/),
    ).toBeInTheDocument();
    expect(screen.queryByText("任务未完成")).not.toBeInTheDocument();
    expect(screen.queryByText("失败原因")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("falls back to the error banner when the failed turn ends in a processing group", () => {
    const messages: Message[] = [
      { id: "user-1", type: "human", content: "first request" },
      { id: "assistant-1", type: "ai", content: "Earlier assistant answer." },
      { id: "user-2", type: "human", content: "second request" },
      {
        id: "assistant-2",
        type: "ai",
        content: "",
        tool_calls: [{ id: "tc-1", name: "web_search", args: { query: "q" } }],
      } as AIMessage,
    ];
    const thread = mockThread({
      messages,
      error: new Error("beak step 3 failed"),
    });

    renderMessageList(thread, "en-US");

    const banner = screen.getByRole("alert");
    expect(banner).toHaveTextContent(
      "This turn stopped before finishing. Continue the chat or retry.",
    );
    expect(banner).not.toHaveTextContent("beak step 3 failed");
  });

  it("shows a structured terminal handoff instead of replacing it with a generic failure", () => {
    const handoff =
      "最终汇总超过了单轮时限。已完成的工具结果仍保留；点击继续可从当前进度重新收敛。";
    const messages: Message[] = [
      { id: "user-1", type: "human", content: "继续完成分析" },
      {
        id: "failed-handoff",
        type: "ai",
        content: "",
        additional_kwargs: {
          response_state: "failed",
          error: {
            message: handoff,
            info: { code: "model_stall", disposition: "failed" },
          },
        },
      },
    ];

    renderMessageList(mockThread({ messages }));

    const receipt = screen.getByText(handoff);
    expect(receipt).toBeInTheDocument();
    expect(receipt.className).not.toContain("truncate");
    expect(receipt.className).toContain("whitespace-pre-wrap");
    expect(
      screen.queryByText("本轮任务未完成。可继续发送消息或重试。"),
    ).not.toBeInTheDocument();
  });

  it("never shows a live activity pulse beside an authoritative failure", () => {
    const messages: Message[] = [
      { id: "user-1", type: "human", content: "调研一个细分赛道" },
      {
        id: "assistant-tools",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "search-1",
            name: "web_search",
            args: { query: "personalized nutrition market" },
          },
        ],
      } as AIMessage,
    ];

    renderMessageList(
      mockThread({
        messages,
        isLoading: true,
        error: new Error("provider stopped before final answer"),
      }),
      "en-US",
    );

    expect(
      screen.queryByTestId("conversation-activity-pulse"),
    ).not.toBeInTheDocument();
  });

  it("does not contradict a settled answer with a stale generic thread error", () => {
    const messages: Message[] = [
      { id: "user-1", type: "human", content: "调研一个细分赛道" },
      {
        id: "assistant-final",
        type: "ai",
        content: "赛道分析已经完成，下面是机会点、竞争格局和风险。",
      },
    ];

    renderMessageList(
      mockThread({
        messages,
        error: new Error("provider connection closed after completion"),
      }),
    );

    expect(
      screen.getByText("赛道分析已经完成，下面是机会点、竞争格局和风险。"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("任务未完成")).not.toBeInTheDocument();
  });

  it("renders historical structured failures as compact receipts with full detail in the workbench", () => {
    const rawDetail = [
      "任务未能完成：内部守卫缺少执行证据。",
      "Code mode cannot finish this implementation task yet: no successful file write/edit execution is recorded.",
    ].join("\n\n");
    const messages: Message[] = [
      { id: "user-1", type: "human", content: "分析代码" },
      {
        id: "failed-1",
        type: "ai",
        content: "",
        additional_kwargs: {
          response_state: "failed",
          error: {
            message: rawDetail,
            info: { code: "agent_response_failed" },
          },
        },
      },
      { id: "user-2", type: "human", content: "继续" },
      { id: "assistant-2", type: "ai", content: "后续任务已经完成。" },
    ];
    renderMessageList(mockThread({ messages }));

    expect(screen.getByText("先前尝试失败，后续已恢复")).toBeInTheDocument();
    expect(
      screen.queryByText(
        "该任务要求修改项目文件，但本轮没有产生有效的文件变更。",
      ),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("任务未完成")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/implementation task yet/),
    ).not.toBeInTheDocument();
    // The "查看过程" button is intentionally not shown on failure — it was
    // borrowed from Kimi's share-case flow but adds no value here. The full
    // detail remains available in the Agent Workbench trace directly.
    expect(
      screen.queryByRole("button", { name: "查看过程" }),
    ).not.toBeInTheDocument();
  });

  it("does not present interrupted drafts as settled answers", () => {
    const messages: Message[] = [
      { id: "user-1", type: "human", content: "继续" },
      {
        id: "interrupted-1",
        type: "ai",
        content: "",
        additional_kwargs: {
          response_state: "interrupted",
          message_kind: "answer",
        },
      },
    ];

    renderMessageList(mockThread({ messages }));

    expect(
      screen.getByText("此回复在生成过程中被中断，可能不完整。"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "好的回复" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "重新生成回复" }),
    ).not.toBeInTheDocument();
  });

  it("mounts audit actions on the final assistant group when changes live in the processing group", () => {
    const human: Message = {
      id: "user-1",
      type: "human",
      content: "改一下配置",
    };
    // file_change lives on the tool-call message, which grouping puts into
    // the processing group — the plain assistant group only carries the
    // final answer text.
    const changeAi: AIMessage = {
      id: "ai-tools",
      type: "ai",
      content: "",
      tool_calls: [
        {
          id: "change-1",
          name: "file_change",
          args: {
            changes: [
              {
                path: "src/config.ts",
                op: "update",
                diff: [
                  "--- a/src/config.ts",
                  "+++ b/src/config.ts",
                  "@@",
                  "-old",
                  "+new",
                ].join("\n"),
              },
            ],
          },
        },
      ],
    };
    const changeResult: ToolMessage = {
      id: "tool-change-1",
      type: "tool",
      content: "ok",
      tool_call_id: "change-1",
    };
    const finalAnswer: Message = {
      id: "ai-final",
      type: "ai",
      content: "配置已经改好了，请审核。",
    };
    const thread = mockThread({
      messages: [human, changeAi, changeResult, finalAnswer],
      error: new Error(
        "Code changes were produced without a verification step",
      ),
    });

    renderMessageList(thread);

    expect(
      screen.queryByRole("button", { name: /撤销/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("审核交给")).not.toBeInTheDocument();
    // The receipt owns the failure display; the fallback banner stays off.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the raw text when thread.error is a plain string", () => {
    const messages: Message[] = [
      { id: "user-1", type: "human", content: "first request" },
      { id: "assistant-1", type: "ai", content: "Earlier assistant answer." },
      { id: "user-2", type: "human", content: "second request" },
      {
        id: "assistant-2",
        type: "ai",
        content: "",
        tool_calls: [{ id: "tc-1", name: "web_search", args: { query: "q" } }],
      } as AIMessage,
    ];
    const thread = mockThread({
      messages,
      error: "boom exploded" as unknown as Error,
    });

    renderMessageList(thread, "en-US");

    const banner = screen.getByRole("alert");
    expect(banner).toHaveTextContent(
      "This turn stopped before finishing. Continue the chat or retry.",
    );
    expect(banner).not.toHaveTextContent("boom exploded");
    expect(banner).not.toHaveTextContent(/This reply was interrupted/i);
  });
});
