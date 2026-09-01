import { act, fireEvent, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import type { AIMessage, Message } from "@/core/api/types";
import type { BaseStream } from "@/core/api/use-stream-types";
import type { AgentThreadState } from "@/core/threads";
import { SubtasksProvider } from "@/core/tasks/context";
import { renderWithProviders } from "@/test/harness";

import type { LiveToolEvent } from "../live-tool-timeline";
import {
  AGENT_WORKBENCH_FOCUS_EVENT,
  AGENT_WORKBENCH_OPEN_EVENT,
  type AgentWorkbenchFocusDetail,
} from "../agent-workbench-events";
import { ThreadProviders } from "./context";
import {
  MESSAGE_LIST_TIMEOUT_WARNING_MS,
  MessageList,
  streamingMessageProgressKey,
} from "./message-list";
import { messageClipboardText } from "./message-list-item";
import {
  deriveInlineSubagents,
  deriveSubagentMissionFromMessages,
  deriveSubagentsFromMessages,
  InlineSubagentCards,
} from "./inline-subagent-cards";

vi.mock("../artifacts", () => ({
  useArtifacts: () => ({
    setOpen: vi.fn(),
    autoOpen: false,
    autoSelect: false,
    selectedArtifact: null,
    select: vi.fn(),
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

function message(id: string, type: Message["type"], content: string): Message {
  return { id, type, content };
}

function toolEvent(
  name: string,
  overrides: Partial<LiveToolEvent> = {},
): LiveToolEvent {
  return {
    id: `${name}-${overrides.status ?? "done"}`,
    name,
    status: "done",
    startedAt: 1000,
    iteration: 1,
    ...overrides,
  };
}

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

function messageListTree({
  thread,
  liveToolEvents = [],
  lastTurnToolEvents = [],
  mode = "code",
  currentAgent = null,
  agentRoster = [],
  completedAgentOutput = false,
  showSenderName = false,
  onAuthorizeNetwork,
}: {
  thread: BaseStream<AgentThreadState>;
  liveToolEvents?: LiveToolEvent[];
  lastTurnToolEvents?: LiveToolEvent[];
  mode?: "chat" | "code";
  completedAgentOutput?: boolean;
  currentAgent?: {
    name: string;
    display_name?: string | null;
    avatar_url?: string | null;
    icon?: string | null;
  } | null;
  agentRoster?: Array<{
    name?: string | null;
    agent_id?: string | null;
    display_name?: string | null;
    avatar_url?: string | null;
    icon?: string | null;
    role?: string | null;
  }>;
  showSenderName?: boolean;
  onAuthorizeNetwork?: (tier: "common" | "full") => void;
}) {
  return (
    <SubtasksProvider>
      <ThreadProviders thread={thread}>
        <MessageList
          threadId="thread-1"
          thread={thread}
          paddingBottom={0}
          liveToolEvents={liveToolEvents}
          lastTurnToolEvents={lastTurnToolEvents}
          mode={mode}
          completedAgentOutput={completedAgentOutput}
          currentAgent={currentAgent}
          agentRoster={agentRoster}
          showSenderName={showSenderName}
          onAuthorizeNetwork={onAuthorizeNetwork}
        />
      </ThreadProviders>
    </SubtasksProvider>
  );
}

function renderMessageList(args: {
  thread: BaseStream<AgentThreadState>;
  liveToolEvents?: LiveToolEvent[];
  lastTurnToolEvents?: LiveToolEvent[];
  mode?: "chat" | "code";
  locale?: "en-US" | "zh-CN";
  currentAgent?: {
    name: string;
    display_name?: string | null;
    avatar_url?: string | null;
    icon?: string | null;
  } | null;
  agentRoster?: Array<{
    name?: string | null;
    agent_id?: string | null;
    display_name?: string | null;
    avatar_url?: string | null;
    icon?: string | null;
    role?: string | null;
  }>;
  showSenderName?: boolean;
  completedAgentOutput?: boolean;
  onAuthorizeNetwork?: (tier: "common" | "full") => void;
}) {
  return renderWithProviders(messageListTree(args), {
    locale: args.locale ?? "en-US",
    initialRoute: "/workspace/realtime/thread-1",
  });
}

describe("MessageList process trace lifecycle", () => {
  test("does not settle an inline agent from child tool completion", () => {
    const base = [
      toolEvent("subagent", {
        id: "spawn-reader",
        lifecycle: "spawned",
        status: "running",
        agentId: "reader",
        subagentCodename: "Quark-d9c",
      }),
      toolEvent("report", {
        id: "report-reader",
        status: "done",
        agentId: "reader",
        subagentCodename: "Quark-d9c",
      }),
      toolEvent("read_file", {
        id: "read-reader",
        status: "error",
        agentId: "reader",
        subagentCodename: "Quark-d9c",
        output: "missing file",
      }),
    ];

    expect(deriveInlineSubagents(base)).toEqual([
      expect.objectContaining({
        id: "reader",
        name: "Quark-d9c",
        status: "running",
        error: undefined,
      }),
    ]);

    expect(
      deriveInlineSubagents([
        ...base,
        toolEvent("subagent", {
          id: "finish-reader",
          lifecycle: "finished",
          status: "done",
          agentId: "reader",
          subagentCodename: "Quark-d9c",
        }),
      ])[0]?.status,
    ).toBe("done");
  });

  test("does not manufacture Agent cards for a batch rejected before spawn", () => {
    const failedDispatch: AIMessage = {
      id: "failed-dispatch",
      type: "ai",
      content: "",
      tool_calls: [
        {
          id: "parallel-invalid",
          name: "call_agent_parallel",
          args: {
            specs: [
              { role: "reader", goal: "read README" },
              { role: "reader", goal: "read pyproject" },
            ],
            output:
              '(工具失败) status=success error=structured_error\n{"ok":false,"count":0}',
          },
        },
      ],
    };

    expect(deriveSubagentsFromMessages([failedDispatch])).toEqual([]);
  });

  test("replays successful legacy role/goal specs with stable identities", () => {
    const legacyDispatch: AIMessage = {
      id: "legacy-dispatch",
      type: "ai",
      content: "",
      tool_calls: [
        {
          id: "parallel-legacy",
          name: "call_agent_parallel",
          args: {
            specs: [
              { role: "schema_reader", goal: "读取 schema" },
              { role: "final_reader", goal: "读取 final" },
            ],
            output: '{"ok":true,"count":2,"succeeded":2,"failed":0}',
          },
        },
      ],
    };

    expect(deriveSubagentsFromMessages([legacyDispatch])).toEqual([
      expect.objectContaining({
        id: "schema_reader",
        name: "schema_reader",
        task: "读取 schema",
        status: "done",
      }),
      expect.objectContaining({
        id: "final_reader",
        name: "final_reader",
        task: "读取 final",
        status: "done",
      }),
    ]);
  });

  test("keeps same-role parallel lanes distinct and merges lifecycle aliases", () => {
    renderWithProviders(
      <InlineSubagentCards
        settled
        agents={[
          {
            id: "reader_readme",
            name: "README 检查",
            role: "explorer",
            status: "running",
            task: "读取 README.md",
            filesTouchedCount: 0,
          },
          {
            id: "reader_pyproject",
            name: "配置检查",
            role: "explorer",
            status: "running",
            task: "读取 pyproject.toml",
            filesTouchedCount: 0,
          },
        ]}
        events={[
          toolEvent("subagent", {
            id: "spawn-readme",
            lifecycle: "spawned",
            status: "running",
            agentId: "reader_readme",
            subAgentRole: "explorer",
            subagentCodename: "Spark-4f6",
          }),
          toolEvent("subagent", {
            id: "finish-readme",
            lifecycle: "finished",
            status: "done",
            agentId: "Spark-4f6",
            subAgentRole: "explorer",
            subagentCodename: "Spark-4f6",
          }),
          toolEvent("subagent", {
            id: "spawn-pyproject",
            lifecycle: "spawned",
            status: "running",
            agentId: "reader_pyproject",
            subAgentRole: "explorer",
            subagentCodename: "Aurora-108",
          }),
          toolEvent("subagent", {
            id: "finish-pyproject",
            lifecycle: "finished",
            status: "done",
            agentId: "Aurora-108",
            subAgentRole: "explorer",
            subagentCodename: "Aurora-108",
          }),
        ]}
      />,
      { locale: "zh-CN" },
    );

    expect(screen.getByText("2 个子 Agent · 2 已完成")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /README 检查/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /配置检查/ }),
    ).toBeInTheDocument();
  });

  test("shows iteration and file counts on the collapsed live card", () => {
    renderWithProviders(
      <InlineSubagentCards
        settled
        agents={[
          {
            id: "schema_reader",
            name: "Rune-3f8",
            role: "explorer",
            status: "done",
            task: "读取 agent-regression.json",
            filesTouchedCount: 3,
            iterationCount: 2,
            index: 0,
          },
        ]}
      />,
      { locale: "zh-CN" },
    );

    const stats = screen.getByTestId("agent-card-stats-0");
    expect(stats).toHaveTextContent("2 次迭代");
    expect(stats).toHaveTextContent("3 文件修改");
  });

  test("expands a completed agent card to reveal the full report", () => {
    const fullReport = `这是一份很长的研究报告，${"深入分析了浙江自然(605080)的行业格局、估值与风险。".repeat(3)}`;
    renderWithProviders(
      <InlineSubagentCards
        settled
        agents={[
          {
            id: "analyst",
            name: "行业分析师",
            role: "analyst",
            status: "done",
            task: "分析浙江自然",
            summary: fullReport,
            filesTouchedCount: 0,
            index: 0,
          },
        ]}
      />,
      { locale: "zh-CN" },
    );

    expect(screen.queryByTestId("agent-report-0")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /查看报告/ }));

    const panel = screen.getByTestId("agent-report-0");
    expect(panel).toHaveTextContent(fullReport);
    expect(
      screen.getByRole("button", { name: /收起报告/ }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /收起报告/ }));
    expect(screen.queryByTestId("agent-report-0")).not.toBeInTheDocument();
  });

  test("expands a failed agent card to reveal the failure reason", () => {
    renderWithProviders(
      <InlineSubagentCards
        settled
        agents={[
          {
            id: "contrarian",
            name: "逆向投资者",
            role: "contrarian",
            status: "error",
            task: "给出反方观点",
            error: "时间窗口不足，未能完成多空验证",
            filesTouchedCount: 0,
            index: 0,
          },
        ]}
      />,
      { locale: "zh-CN" },
    );

    expect(screen.queryByTestId("agent-report-0")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /查看失败原因/ }));

    const panel = screen.getByTestId("agent-report-0");
    expect(panel).toHaveTextContent("时间窗口不足，未能完成多空验证");
  });

  test("folds persisted spec, spawn and finish rows into two compact cards", () => {
    const history: AIMessage = {
      id: "ai-real-history",
      type: "ai",
      content: "done",
      tool_calls: [
        {
          id: "parallel-real",
          name: "call_agent_parallel",
          args: {
            specs: [
              { agent_id: "ui_reader_a", prompt: "读取 README.md 第一行" },
              {
                agent_id: "ui_reader_b",
                prompt: "读取 pyproject.toml 第一行",
              },
            ],
            output: "2/2 sub-agents succeeded",
          },
        },
        {
          id: "spawn-a",
          name: "runtime.__subagent_spawned__",
          args: {
            requested_agent_id: "ui_reader_a",
            agent_id: "explorer",
            role: "explorer",
            codename: "Volt-f84",
            status: "inProgress",
            prompt_preview:
              "# Role: ui_reader_a\n\nInternal role wrapper.\n\n---\n\n读取 README.md 第一行",
          },
        },
        {
          id: "finish-a",
          name: "runtime.__subagent_finished__",
          args: {
            requested_agent_id: "ui_reader_a",
            agent_id: "explorer",
            role: "explorer",
            codename: "Volt-f84",
            ok: true,
            output: "# Echo-Agent",
          },
        },
        {
          id: "spawn-b",
          name: "runtime.__subagent_spawned__",
          args: {
            requested_agent_id: "ui_reader_b",
            agent_id: "explorer",
            role: "explorer",
            codename: "Zenith-6b7",
            status: "inProgress",
            prompt_preview:
              "# Role: ui_reader_b\n\nInternal role wrapper.\n\n---\n\n读取 pyproject.toml 第一行",
          },
        },
        {
          id: "finish-b",
          name: "runtime.__subagent_finished__",
          args: {
            requested_agent_id: "ui_reader_b",
            agent_id: "explorer",
            role: "explorer",
            codename: "Zenith-6b7",
            ok: true,
            output: "[project]",
          },
        },
        {
          id: "auto-failure",
          name: "subagent",
          args: {
            subagent_id: "task-auto-failed",
            role: "general-purpose",
            status: "failed",
            error: "empty_result_contract_violation",
          },
        },
      ],
    };

    const agents = deriveSubagentsFromMessages([history]);
    expect(agents).toHaveLength(2);
    expect(agents.map((agent) => agent.id)).toEqual([
      "ui_reader_a",
      "ui_reader_b",
    ]);
    expect(agents.map((agent) => agent.name)).toEqual([
      "Volt-f84",
      "Zenith-6b7",
    ]);
    expect(agents.map((agent) => agent.task)).toEqual([
      "读取 README.md 第一行",
      "读取 pyproject.toml 第一行",
    ]);
  });

  test("groups delegated agents into one cluster instead of repeating delegation rows", () => {
    const focusEvents: AgentWorkbenchFocusDetail[] = [];
    const handleFocus = (event: Event) => {
      focusEvents.push(
        (event as CustomEvent<AgentWorkbenchFocusDetail>).detail,
      );
    };
    window.addEventListener(AGENT_WORKBENCH_FOCUS_EVENT, handleFocus);

    const user = message("user-cluster", "human", "并行审计项目");
    const processing: AIMessage = {
      id: "ai-cluster",
      type: "ai",
      content: "",
      tool_calls: [
        {
          id: "delegate-reviewer",
          name: "delegate_agent",
          args: {
            agent_id: "reviewer",
            name: "Prism-fcc",
            role: "reviewer",
            prompt: "审查前端交互与视觉回归",
          },
        },
        {
          id: "read-project",
          name: "read_file",
          args: { path: "README.md" },
        },
      ],
    };
    const thread = mockThread({
      messages: [user, processing],
      isLoading: true,
      streamingMessage: processing,
    });

    renderMessageList({
      thread,
      mode: "chat",
      locale: "zh-CN",
      liveToolEvents: [
        toolEvent("subagent", {
          id: "subagent-reviewer",
          status: "running",
          lifecycle: "spawned",
          agentId: "reviewer-runtime",
          subAgentRole: "reviewer",
          subagentCodename: "Prism-fcc",
          input: { prompt: "审查前端交互与视觉回归" },
        }),
      ],
    });

    expect(screen.getByText("Agent 集群")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Prism-fcc/ }),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText("审查前端交互与视觉回归").length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText("委派任务")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Prism-fcc/ }));
    expect(focusEvents).toEqual([
      {
        agentId: "reviewer",
        agent: expect.objectContaining({
          id: "reviewer",
          name: "Prism-fcc",
          role: "reviewer",
          status: "running",
          task: "审查前端交互与视觉回归",
        }),
        turnIndex: 0,
        tab: "agent",
        view: "screen",
      },
    ]);
    expect(screen.queryByTestId("agent-hover-0")).not.toBeInTheDocument();

    window.removeEventListener(AGENT_WORKBENCH_FOCUS_EVENT, handleFocus);
  });

  test("recovers a replayed cluster mission and settles stale running agents", () => {
    const orchestration: AIMessage = {
      id: "orchestration-history",
      type: "ai",
      content: "",
      tool_calls: [
        {
          id: "run-history",
          name: "run_orchestration",
          args: {
            goal: "对 echo-agent 仓库做深度审计（只读，禁止写任何文件）。后续细节不应挤进卡片。",
          },
        },
      ],
    };
    const mission = deriveSubagentMissionFromMessages([orchestration]);

    expect(mission).toBe(
      "对 echo-agent 仓库做深度审计（只读，禁止写任何文件）。",
    );
    renderWithProviders(
      <InlineSubagentCards
        settled
        mission={mission}
        agents={[
          {
            id: "historic-agent",
            name: "Prism-history",
            role: "reviewer",
            status: "running",
            task: "",
            summary: "ConnectError: upstream connection failed",
            filesTouchedCount: 0,
            index: 0,
          },
        ]}
      />,
      { locale: "zh-CN" },
    );

    expect(screen.getByText("1 个子 Agent · 1 异常")).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: /Prism-history.*对 echo-agent 仓库做深度审计.*异常/,
      }),
    ).toBeInTheDocument();
  });

  test("collapses large agent clusters to six rows and restores the full list", () => {
    const user = message("user-large-cluster", "human", "并行执行七项检查");
    const processing: AIMessage = {
      id: "ai-large-cluster",
      type: "ai",
      content: "",
      tool_calls: Array.from({ length: 7 }, (_, index) => ({
        id: `delegate-${index + 1}`,
        name: "delegate_agent",
        args: {
          agent_id: `agent-${index + 1}`,
          name: `Agent ${String(index + 1).padStart(2, "0")}`,
          role: "researcher",
          prompt: `执行检查 ${index + 1}`,
        },
      })),
    };

    renderMessageList({
      thread: mockThread({
        messages: [user, processing],
        isLoading: true,
        streamingMessage: processing,
      }),
      mode: "chat",
      locale: "zh-CN",
    });

    expect(
      screen.getByRole("button", { name: /Agent 06/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Agent 07/ }),
    ).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "展开其余 1 个智能体" }),
    );
    expect(
      screen.getByRole("button", { name: /Agent 07/ }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "收起智能体" }));
    expect(
      screen.queryByRole("button", { name: /Agent 07/ }),
    ).not.toBeInTheDocument();
  });

  test("keeps one coherent assistant lane across a long-task lifecycle", () => {
    const agent = {
      name: "general",
      display_name: "Eve",
      avatar_url: "/api/agents/general/avatar",
    };
    const user = message("user-long", "human", "完成一项多阶段市场调研");
    const initialThread = mockThread({
      messages: [user],
      isLoading: true,
    });
    const { rerender } = renderMessageList({
      thread: initialThread,
      mode: "chat",
      locale: "zh-CN",
      currentAgent: agent,
    });

    expect(
      screen.getByTestId("conversation-activity-pulse"),
    ).toBeInTheDocument();
    expect(screen.getAllByAltText("Eve")).toHaveLength(1);

    const planning: AIMessage = {
      id: "assistant-plan",
      type: "ai",
      content: "我先确定赛道，再核对市场与竞品证据。",
      additional_kwargs: {
        public_progress: true,
        phase_id: "turn-long:phase-1",
        agent_id: "general",
        agent_display_name: "Eve",
      },
    };
    const searchCall: AIMessage = {
      id: "assistant-search",
      type: "ai",
      content: "",
      tool_calls: [
        {
          id: "search-long",
          name: "web_search",
          args: { query: "2026 AI mental health market" },
          phaseId: "turn-long:phase-1",
        },
      ],
      additional_kwargs: {
        agent_id: "general",
        agent_display_name: "Eve",
      },
    };
    rerender(
      messageListTree({
        thread: mockThread({
          messages: [user, planning, searchCall],
          isLoading: true,
        }),
        mode: "chat",
        currentAgent: agent,
        liveToolEvents: [
          toolEvent("web_search", {
            id: "search-long",
            status: "running",
            input: { query: "2026 AI mental health market" },
          }),
        ],
      }),
    );

    expect(
      screen.getByText("我先确定赛道，再核对市场与竞品证据。"),
    ).toBeInTheDocument();
    expect(screen.getByText("搜索网页")).toBeInTheDocument();
    expect(
      screen.getByTestId("conversation-activity-pulse"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/web_search/)).not.toBeInTheDocument();
    expect(screen.getAllByAltText("Eve")).toHaveLength(1);

    const searchResult = {
      id: "search-result-long",
      type: "tool",
      content: '{"success":true,"count":8}',
      tool_call_id: "search-long",
    } as Message;
    const finalAnswer: AIMessage = {
      id: "assistant-final-long",
      type: "ai",
      content: "调研完成：建议优先进入校园心理健康基础设施赛道。",
      additional_kwargs: {
        agent_id: "general",
        agent_display_name: "Eve",
      },
    };
    rerender(
      messageListTree({
        thread: mockThread({
          messages: [user, planning, searchCall, searchResult, finalAnswer],
        }),
        mode: "chat",
        currentAgent: agent,
        completedAgentOutput: true,
        lastTurnToolEvents: [
          toolEvent("web_search", {
            id: "search-long",
            status: "done",
            input: { query: "2026 AI mental health market" },
            output: { success: true, count: 8 },
          }),
        ],
      }),
    );

    expect(
      screen.getByText("调研完成：建议优先进入校园心理健康基础设施赛道。"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("conversation-activity-pulse"),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getAllByAltText("Eve")).toHaveLength(1);
  });

  test("keeps hidden user actions out of the document flow", () => {
    const thread = mockThread({
      messages: [message("user-1", "human", "继续")],
    });

    renderMessageList({ thread });

    const editButton = screen.getByRole("button", {
      name: "Edit and resend",
    });
    expect(editButton.parentElement).toHaveClass("absolute", "top-full");
  });

  test("uses team roster avatars for assistant messages without avatar metadata", () => {
    const thread = mockThread({
      messages: [
        message("user-1", "human", "你好"),
        message("assistant-1", "ai", "你好，我是 Eve。"),
      ],
    });

    renderMessageList({
      thread,
      mode: "chat",
      showSenderName: true,
      agentRoster: [
        {
          name: "general",
          display_name: "Eve",
          avatar_url: "/api/agents/general/avatar",
          role: "tl",
        },
      ],
    });

    expect(screen.getByText("Eve")).toBeInTheDocument();
    expect(screen.getByText("队长")).toBeInTheDocument();
    expect(screen.getByAltText("Eve")).toHaveAttribute(
      "src",
      expect.stringContaining("/api/agents/general/avatar"),
    );
  });

  test("keeps model strategy and token metadata out of the main transcript", () => {
    const thread = mockThread({
      messages: [
        message("user-1", "human", "总结一下"),
        {
          id: "assistant-1",
          type: "ai",
          content: "已完成总结。",
          additional_kwargs: {
            echo: {
              strategy: "react_loop",
              success: true,
              input_tokens: 1200,
              output_tokens: 480,
              duration_ms: 8300,
              rounds: 3,
            },
          },
        } as Message,
      ],
    });

    renderMessageList({ thread, mode: "chat", locale: "zh-CN" });

    expect(screen.getByText("已完成总结。")).toBeInTheDocument();
    expect(
      screen.queryByText(/ReAct|直答|反射|缓存|投票/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/1\.2k|480|8\.3s|rounds|tokens/i),
    ).not.toBeInTheDocument();
  });

  test("copies only visible public assistant text, never raw reasoning", () => {
    const assistant = {
      id: "assistant-private",
      type: "ai",
      content:
        "<details><summary>Thinking</summary>private chain</details><read_only> </read_only>\n<TextBlock>已确认事实。</TextBlock>",
      additional_kwargs: {
        reasoning_content: "PRIVATE_REASONING_DO_NOT_COPY",
      },
    } as unknown as Message;

    expect(messageClipboardText(assistant)).toBe("已确认事实。");

    const reasoningOnly = {
      id: "assistant-reasoning-only",
      type: "ai",
      content: "",
      additional_kwargs: {
        reasoning_content: "PRIVATE_REASONING_DO_NOT_COPY",
      },
    } as unknown as Message;
    expect(messageClipboardText(reasoningOnly)).toBe("");

    const publicSummaryOnly = {
      id: "assistant-public-summary",
      type: "ai",
      content: "",
      additional_kwargs: {
        reasoning_content: "PRIVATE_REASONING_DO_NOT_COPY",
        public_reasoning_summary: "我先核对两个文件。",
      },
    } as unknown as Message;
    expect(messageClipboardText(publicSummaryOnly)).toBe("我先核对两个文件。");

    const untrustedAlias = {
      id: "assistant-untrusted-summary-alias",
      type: "ai",
      content: "",
      additional_kwargs: {
        reasoning_summary: "接下来我会检查配置。",
      },
    } as AIMessage;
    expect(messageClipboardText(untrustedAlias)).toBe("");
  });

  test("shows the assistant avatar before the first model event arrives", () => {
    const thread = mockThread({
      messages: [message("user-1", "human", "分析这两个文件")],
      isLoading: true,
      streamingMessage: null,
    });

    renderMessageList({
      thread,
      currentAgent: {
        name: "general",
        display_name: "Eve",
        avatar_url: "/api/agents/general/avatar",
      },
    });

    expect(
      screen.getByTestId("conversation-activity-pulse"),
    ).toBeInTheDocument();
    expect(screen.getAllByAltText("Eve")).toHaveLength(1);
  });

  test("shows the assistant avatar even before the optimistic user row mounts", () => {
    const thread = mockThread({
      messages: [],
      isLoading: true,
      streamingMessage: null,
    });

    renderMessageList({
      thread,
      currentAgent: {
        name: "general",
        display_name: "Eve",
        avatar_url: "/api/agents/general/avatar",
      },
    });

    expect(
      screen.getByTestId("conversation-activity-pulse"),
    ).toBeInTheDocument();
    expect(screen.getAllByAltText("Eve")).toHaveLength(1);
  });

  test("keeps waiting status attached to the existing assistant lane", () => {
    const thread = mockThread({
      messages: [
        message("user-1", "human", "继续核对"),
        {
          id: "assistant-progress",
          type: "ai",
          content: "我先核对相关上下文。",
          additional_kwargs: {
            public_progress: true,
            agent_id: "general",
            agent_display_name: "Eve",
          },
        } as AIMessage,
      ],
      isLoading: true,
      streamingMessage: null,
    });

    renderMessageList({
      thread,
      locale: "zh-CN",
      currentAgent: {
        name: "general",
        display_name: "Eve",
        avatar_url: "/api/agents/general/avatar",
      },
    });

    expect(screen.getAllByAltText("Eve")).toHaveLength(1);
    const continuation = screen.getByTestId("assistant-continuation-activity");
    expect(continuation).toHaveClass("ml-11");
    expect(
      screen.getByTestId("conversation-activity-pulse"),
    ).toBeInTheDocument();
  });

  test("shows one avatar across adjacent process and answer groups from the same agent", () => {
    const thread = mockThread({
      messages: [
        message("user-1", "human", "检查两个文件"),
        {
          id: "assistant-progress",
          type: "ai",
          content: "我先核对两个文件。",
          additional_kwargs: {
            public_progress: true,
            agent_id: "general",
            agent_display_name: "Eve",
          },
        } as AIMessage,
        {
          id: "assistant-final",
          type: "ai",
          content: "两个文件的字段定义一致。",
          additional_kwargs: {
            agent_id: "general",
            agent_display_name: "Eve",
          },
        } as AIMessage,
      ],
    });

    renderMessageList({
      thread,
      currentAgent: {
        name: "general",
        display_name: "Eve",
        avatar_url: "/api/agents/general/avatar",
      },
    });

    expect(screen.getAllByAltText("Eve")).toHaveLength(1);
    expect(screen.getByText("我先核对两个文件。")).toBeInTheDocument();
    expect(screen.getByText("两个文件的字段定义一致。")).toBeInTheDocument();
  });

  test("keeps one avatar when final answer identity metadata arrives incomplete", () => {
    const thread = mockThread({
      messages: [
        message("user-1", "human", "检查两个文件"),
        {
          id: "assistant-progress",
          type: "ai",
          content: "我先核对两个文件。",
          additional_kwargs: {
            public_progress: true,
            agent_id: "general",
            agent_display_name: "Eve",
            agent_avatar_url: "/api/agents/general/avatar",
          },
        } as AIMessage,
        message("assistant-final", "ai", "两个文件的字段定义一致。"),
      ],
    });

    const { container } = renderMessageList({ thread });

    expect(container.querySelectorAll(".size-8.rounded-md")).toHaveLength(1);
    expect(screen.getByText("我先核对两个文件。")).toBeInTheDocument();
    expect(screen.getByText("两个文件的字段定义一致。")).toBeInTheDocument();
  });

  test("keeps one evidence cluster inside each conversational interval", () => {
    const agentMetadata = {
      agent_id: "general",
      agent_display_name: "Eve",
    };
    const thread = mockThread({
      messages: [
        message("user-1", "human", "检查结构化时间线"),
        {
          id: "progress-opening",
          type: "ai",
          content: "我先核对前端如何归并三条事件通道。",
          additional_kwargs: {
            public_progress: true,
            timeline_sequence: 1,
            ...agentMetadata,
          },
        } as AIMessage,
        {
          id: "tools-frontend",
          type: "ai",
          content: "",
          additional_kwargs: agentMetadata,
          tool_calls: [
            {
              id: "read-adapter",
              name: "read_file",
              args: { path: "frontend/src/core/threads/realtime-adapter.ts" },
              timelineSequence: 2,
            },
            {
              id: "read-group",
              name: "read_file",
              args: {
                path: "frontend/src/components/workspace/messages/message-group.tsx",
              },
              timelineSequence: 3,
            },
          ],
        } as AIMessage,
        {
          id: "progress-finding",
          type: "ai",
          content: "前端边界已经确认，现在转向服务端事件桥。",
          additional_kwargs: {
            public_progress: true,
            timeline_sequence: 4,
            ...agentMetadata,
          },
        } as AIMessage,
        {
          id: "tools-runtime",
          type: "ai",
          content: "",
          additional_kwargs: agentMetadata,
          tool_calls: [
            {
              id: "read-bridge",
              name: "read_file",
              args: {
                path: "runtime/sensing/gateway/realtime_event_bridge.py",
              },
              timelineSequence: 5,
            },
          ],
        } as AIMessage,
        {
          id: "assistant-final",
          type: "ai",
          content: "三条通道最终按结构化坐标合并。",
          additional_kwargs: agentMetadata,
        } as AIMessage,
      ],
    });

    renderMessageList({
      thread,
      locale: "zh-CN",
      currentAgent: {
        name: "general",
        display_name: "Eve",
        avatar_url: "/api/agents/general/avatar",
      },
    });

    const commentary = screen.getAllByTestId("public-progress-event");
    const execution = screen.getAllByTestId("process-timeline-event-execution");
    const answer = screen.getByText("三条通道最终按结构化坐标合并。");

    expect(commentary).toHaveLength(2);
    expect(execution).toHaveLength(2);
    expect(execution[0]).toHaveTextContent(
      "realtime-adapter.ts · message-group.tsx",
    );
    expect(execution[1]).toHaveTextContent("realtime_event_bridge.py");
    expect(
      commentary[0]!.compareDocumentPosition(execution[0]!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      execution[0]!.compareDocumentPosition(commentary[1]!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      commentary[1]!.compareDocumentPosition(execution[1]!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      execution[1]!.compareDocumentPosition(answer) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.getAllByAltText("Eve")).toHaveLength(1);
  });

  test("keeps intent and final fact anchors when compacting a long public timeline", () => {
    const agentMetadata = {
      agent_id: "general",
      agent_display_name: "Eve",
    };
    const messages: Message[] = [message("user-1", "human", "跑一个长任务")];
    for (let index = 0; index < 6; index += 1) {
      messages.push({
        id: `progress-${index}`,
        type: "ai",
        content:
          index === 0
            ? "我先判断任务边界。"
            : index === 5
              ? "已确认关键事实，可以收束。"
              : `中间公开检查点 ${index}`,
        additional_kwargs: {
          public_progress: true,
          timeline_sequence: index * 2 + 1,
          ...agentMetadata,
        },
      } as AIMessage);
      messages.push({
        id: `tool-${index}`,
        type: "ai",
        content: "",
        additional_kwargs: agentMetadata,
        tool_calls: [
          {
            id: `read-${index}`,
            name: "read_file",
            args: { path: `frontend/src/file-${index}.ts` },
            timelineSequence: index * 2 + 2,
          },
        ],
      } as AIMessage);
    }
    messages.push({
      id: "assistant-final",
      type: "ai",
      content: "最终回答。",
      additional_kwargs: agentMetadata,
    } as AIMessage);

    const thread = mockThread({ messages });
    renderMessageList({ thread, locale: "zh-CN" });

    // Completed long traces are intentionally folded by default. Expand the
    // replay before asserting its internal semantic sampling contract.
    fireEvent.click(screen.getByTestId("process-replay-toggle"));

    const commentary = screen.getAllByTestId("public-progress-event");
    const execution = screen.getAllByTestId("process-timeline-event-execution");
    expect(commentary).toHaveLength(4);
    expect(execution.length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText("我先判断任务边界。")).toBeInTheDocument();
    expect(screen.getByText("已确认关键事实，可以收束。")).toBeInTheDocument();
    expect(screen.queryByText("中间公开检查点 2")).not.toBeInTheDocument();
    expect(screen.queryByText("中间公开检查点 3")).not.toBeInTheDocument();
    expect(screen.getByText("最终回答。")).toBeInTheDocument();
  });

  test("does not repeat an avatar when visual metadata arrives mid-turn", () => {
    const thread = mockThread({
      messages: [
        message("user-1", "human", "检查文件"),
        {
          id: "assistant-progress",
          type: "ai",
          content: "正在检查。",
          additional_kwargs: {
            public_progress: true,
            agent_id: "general",
            agent_display_name: "Eve",
          },
        } as AIMessage,
        {
          id: "assistant-final",
          type: "ai",
          content: "检查完成。",
          additional_kwargs: {
            agent_id: "general",
            agent_display_name: "Eve",
            agent_avatar_url: "/api/agents/general/avatar-v2",
          },
        } as AIMessage,
      ],
    });

    renderMessageList({
      thread,
      currentAgent: {
        name: "general",
        display_name: "Eve",
        avatar_url: "/api/agents/general/avatar-v1",
      },
    });

    expect(screen.getAllByAltText("Eve")).toHaveLength(1);
  });

  test("shows a new avatar when the speaking agent changes within a turn", () => {
    const thread = mockThread({
      messages: [
        message("user-1", "human", "协作检查"),
        {
          id: "assistant-progress",
          type: "ai",
          content: "Eve 正在核对。",
          additional_kwargs: {
            public_progress: true,
            agent_id: "general",
            agent_display_name: "Eve",
          },
        } as AIMessage,
        {
          id: "assistant-final",
          type: "ai",
          content: "Coder 已完成复核。",
          additional_kwargs: {
            agent_id: "coder",
            agent_display_name: "Coder",
          },
        } as AIMessage,
      ],
    });

    renderMessageList({
      thread,
      showSenderName: true,
      agentRoster: [
        {
          name: "general",
          display_name: "Eve",
          avatar_url: "/api/agents/general/avatar",
        },
        {
          name: "coder",
          display_name: "Coder",
          avatar_url: "/api/agents/coder/avatar",
        },
      ],
    });

    expect(screen.getByAltText("Eve")).toBeInTheDocument();
    expect(screen.getByAltText("Coder")).toBeInTheDocument();
  });

  test("does not render a completed process trace block in chat answers", () => {
    const messages = [
      message("user-1", "human", "What is NAS?"),
      message("assistant-1", "ai", "NAS is network-attached storage."),
    ];
    const thread = mockThread({ messages });

    renderMessageList({
      thread,
      lastTurnToolEvents: [
        toolEvent("read_file", {
          id: "read-1",
          input: { path: "notes/nas.md" },
        }),
      ],
      mode: "chat",
    });

    expect(
      screen.getByText("NAS is network-attached storage."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Open details/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Thinking process/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Read notes\/nas\.md/)).not.toBeInTheDocument();
  });

  test("keeps completed process traces compact even when they are the latest turn", () => {
    const oldTrace: AIMessage = {
      id: "assistant-old",
      type: "ai",
      content: "",
      additional_kwargs: {
        public_reasoning_summary: "Inspect the old market request.",
      },
      tool_calls: [
        {
          id: "old-search",
          name: "web_search",
          args: { query: "old market query" },
        },
      ],
    };
    const latestTrace: AIMessage = {
      id: "assistant-latest",
      type: "ai",
      content: "",
      additional_kwargs: {
        public_reasoning_summary: "Inspect the latest market request.",
      },
      tool_calls: [
        {
          id: "latest-search",
          name: "web_search",
          args: { query: "latest market query" },
        },
      ],
    };
    const messages: Message[] = [
      message("user-old", "human", "first request"),
      oldTrace,
      message("user-latest", "human", "second request"),
      latestTrace,
    ];
    const thread = mockThread({ messages });

    renderMessageList({ thread });

    expect(
      screen.queryByTestId("process-details-trigger"),
    ).not.toBeInTheDocument();
    const savedStepRows = screen.getAllByTestId(
      "process-timeline-event-execution",
    );
    expect(savedStepRows).toHaveLength(2);
    expect(screen.getByText(/old market query/)).toBeInTheDocument();
    // Explicit public summaries render as compact one-line process rows.
    expect(
      screen.getByText("Inspect the old market request."),
    ).toBeInTheDocument();
    expect(screen.getByText(/latest market query/)).toBeInTheDocument();
    expect(
      screen.getByText("Inspect the latest market request."),
    ).toBeInTheDocument();

    fireEvent.click(savedStepRows[0]!);

    expect(screen.getAllByText(/old market query/).length).toBeGreaterThan(0);
    expect(screen.getByText(/latest market query/)).toBeInTheDocument();
  });

  test("keeps streaming details in the workbench instead of expanding the transcript", () => {
    const oldTrace: AIMessage = {
      id: "assistant-old",
      type: "ai",
      content: "",
      additional_kwargs: {
        public_reasoning_summary: "Inspect the old market request.",
      },
      tool_calls: [
        {
          id: "old-search",
          name: "web_search",
          args: { query: "old market query" },
        },
      ],
    };
    const latestTrace: AIMessage = {
      id: "assistant-latest",
      type: "ai",
      content: "",
      additional_kwargs: {
        public_reasoning_summary:
          "Inspect the actively streaming market request.",
      },
      tool_calls: [
        {
          id: "latest-search",
          name: "web_search",
          args: { query: "active market query" },
        },
      ],
    };
    const messages: Message[] = [
      message("user-old", "human", "first request"),
      oldTrace,
      message("user-latest", "human", "second request"),
      latestTrace,
    ];
    const thread = mockThread({
      messages,
      streamingMessage: latestTrace,
      isLoading: true,
    });

    renderMessageList({ thread });

    // Old trace renders (collapsed) + streaming trace keeps its own row;
    // conversation rows no longer duplicate the global workbench control.
    expect(
      screen.queryByTestId("process-details-trigger"),
    ).not.toBeInTheDocument();
    expect(
      screen.getAllByTestId("process-timeline-event-execution").length,
    ).toBeGreaterThan(0);
    expect(screen.getByText(/old market query/)).toBeInTheDocument();
    expect(screen.getAllByText(/active market query/).length).toBeGreaterThan(
      0,
    );
    // Streaming public summaries stay compact; details live in the workbench.
    expect(
      screen.getByText("Inspect the actively streaming market request."),
    ).toBeInTheDocument();
  });

  test("pins the Kimi-style stream tail to only the active answer", () => {
    const assistant = message("assistant-stream", "ai", "正在构建项目概览");
    const activeThread = mockThread({
      messages: [message("user-1", "human", "继续"), assistant],
      streamingMessage: assistant,
      isLoading: true,
    });
    const { container, rerender } = renderMessageList({ thread: activeThread });

    expect(container.querySelectorAll(".kimi-streaming-tail")).toHaveLength(1);

    rerender(
      messageListTree({
        thread: mockThread({ messages: activeThread.messages }),
      }),
    );

    expect(container.querySelector(".kimi-streaming-tail")).toBeNull();
  });

  test("keeps a short tool-backed answer in one stable streaming lane", () => {
    const assistant: AIMessage = {
      id: "assistant-stream",
      type: "ai",
      content: "正在收束结论",
      additional_kwargs: {
        message_kind: "answer",
        agent_id: "general",
        agent_display_name: "Eve",
      },
      tool_calls: [
        {
          id: "read-1",
          name: "read_file",
          args: { path: "runtime/protocol/items.py" },
        },
      ],
    };
    const thread = mockThread({
      messages: [message("user-1", "human", "继续"), assistant],
      streamingMessage: assistant,
      isLoading: true,
    });
    const { container } = renderMessageList({
      thread,
      currentAgent: {
        name: "general",
        display_name: "Eve",
        avatar_url: "/api/agents/general/avatar",
      },
    });

    expect(screen.getAllByText("正在收束结论")).toHaveLength(1);
    expect(container.querySelectorAll(".kimi-streaming-tail")).toHaveLength(1);
    expect(
      screen.getAllByTestId("process-timeline-event-execution").length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByAltText("Eve")).toHaveLength(1);
  });

  test("keeps the current action visible before the first answer token", () => {
    const activeThread = mockThread({
      messages: [message("user-1", "human", "帮我检查项目")],
      isLoading: true,
    });
    const { rerender } = renderMessageList({
      thread: activeThread,
      mode: "chat",
      liveToolEvents: [
        toolEvent("read_file", {
          id: "read-live",
          status: "running",
          input: { path: "src/app.ts" },
        }),
      ],
    });

    const pulse = screen.getByTestId("conversation-activity-pulse");
    expect(pulse).toHaveTextContent("Read file: app.ts");
    expect(pulse).not.toHaveTextContent("Thinking...");

    rerender(
      messageListTree({
        thread: mockThread({
          messages: [
            ...activeThread.messages,
            message("assistant-progress", "ai", "正在核对 src/app.ts"),
          ],
          isLoading: true,
        }),
        mode: "chat",
        liveToolEvents: [
          toolEvent("read_file", {
            id: "read-live",
            status: "running",
            input: { path: "src/app.ts" },
          }),
        ],
      }),
    );

    expect(screen.getByTestId("conversation-activity-pulse")).toHaveTextContent(
      "Read file: app.ts",
    );

    rerender(
      messageListTree({
        thread: mockThread({ messages: activeThread.messages }),
        mode: "chat",
      }),
    );

    expect(
      screen.queryByTestId("conversation-activity-pulse"),
    ).not.toBeInTheDocument();
  });

  test("does not mark a delivered run red for partial tool failures", () => {
    const messages: Message[] = [
      message("user-1", "human", "Write a research report"),
      message("assistant-1", "ai", "Report artifact generated."),
    ];
    const thread = mockThread({
      messages,
      error: new Error("some web searches failed"),
    });
    const { container } = renderMessageList({
      thread,
      completedAgentOutput: true,
      lastTurnToolEvents: [
        toolEvent("web_search", {
          id: "search-error",
          status: "error",
          input: { query: "source timeout" },
        }),
        toolEvent("write_file", {
          id: "write-final",
          status: "done",
          input: { path: "/tmp/workspace/output/final/report.md" },
        }),
      ],
    });

    expect(
      container.querySelector('[data-turn-marker-status="error"]'),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Report artifact generated.")).toBeInTheDocument();
  });
});

describe("streamingMessageProgressKey", () => {
  test("tracks string growth and same-length tail revisions", () => {
    const first = message("assistant-1", "ai", "A".repeat(300) + "tail-a");
    const grown = message("assistant-1", "ai", "A".repeat(301) + "tail-a");
    const revised = message("assistant-1", "ai", "A".repeat(300) + "tail-b");

    expect(streamingMessageProgressKey(first)).not.toBe(
      streamingMessageProgressKey(grown),
    );
    expect(streamingMessageProgressKey(first)).not.toBe(
      streamingMessageProgressKey(revised),
    );
  });

  test("reads text parts without including image payloads", () => {
    const withImage: Message = {
      id: "assistant-1",
      type: "ai",
      content: [
        { type: "text", text: "hello" },
        { type: "image_url", image_url: "data:image/png;base64,large" },
      ],
    };

    expect(streamingMessageProgressKey(withImage)).toBe("5:hello");
  });
});

describe("MessageList reasoning privacy", () => {
  test("does not expose raw reasoning content when no public summary exists", () => {
    const privateChineseReasoning =
      '\u7528\u6237\u95ee\u6211"\u4f60\u662f\u8c01"\uff0c\u6839\u636e\u6211\u7684\u8eab\u4efd\uff0c\u6211\u5e94\u8be5\u56de\u7b54"\u6211\u662f Echo"\u3002';
    const assistant: AIMessage = {
      id: "assistant-private-reasoning",
      type: "ai",
      content: "\u6211\u662f Echo\u3002",
      additional_kwargs: {
        reasoning_content: [
          privateChineseReasoning,
          'The user asks "who are you".',
          "Per SOUL.md and the hard system rule, I should answer as the current agent persona.",
        ].join("\n"),
      },
    };
    const thread = mockThread({
      messages: [message("user-1", "human", "\u4f60\u662f\u8c01"), assistant],
    });

    renderMessageList({ thread, locale: "zh-CN" });

    expect(screen.getByText("\u6211\u662f Echo\u3002")).toBeInTheDocument();
    expect(screen.queryByText(/用户问我/)).not.toBeInTheDocument();
    expect(screen.queryByText(/SOUL\.md/)).not.toBeInTheDocument();
  });

  test("surfaces a reasoning-only streaming message as a thinking row", () => {
    const leakedReasoning =
      "好的，用户之前发了很长一段关于我是Echo的系统指令，现在又发了当前日期，我应该先判断是否需要工具。";
    const assistant: AIMessage = {
      id: "assistant-reasoning-only",
      type: "ai",
      content: "",
      additional_kwargs: {
        reasoning_content: leakedReasoning,
      },
    };
    const thread = mockThread({
      messages: [message("user-1", "human", "请只回复收到"), assistant],
      streamingMessage: assistant,
      isLoading: true,
    });

    renderMessageList({ thread, locale: "zh-CN" });

    // Streaming UX: what the user saw live (thinking row) stays visible on
    // replay — raw reasoning is no longer hidden. The row is collapsible
    // and dimmed (inner chain-of-thought), while the pulse still indicates
    // the answer is on its way.
    expect(screen.getByText(/系统指令/)).toBeInTheDocument();
    expect(screen.getByText(/当前日期/)).toBeInTheDocument();
    expect(
      screen.getByTestId("conversation-activity-pulse"),
    ).toBeInTheDocument();
  });

  test("renders explicitly public reasoning summaries without exposing raw reasoning", () => {
    const publicSummary =
      "\u6211\u4f1a\u7b80\u8981\u8bf4\u660e\u8eab\u4efd\u3002";
    const assistant: AIMessage = {
      id: "assistant-public-summary",
      type: "ai",
      content: "\u6211\u662f Echo\u3002",
      additional_kwargs: {
        reasoning_content:
          "Per SOUL.md and the hard system rule, keep this private.",
        public_reasoning_summary: publicSummary,
      },
    };
    const thread = mockThread({
      messages: [message("user-1", "human", "\u4f60\u662f\u8c01"), assistant],
      streamingMessage: assistant,
      isLoading: true,
    });

    renderMessageList({ thread, locale: "zh-CN" });

    expect(
      screen.queryByText(/\u601d\u8003\u8fc7\u7a0b/),
    ).not.toBeInTheDocument();
    expect(screen.getByText(publicSummary)).toBeInTheDocument();
    expect(screen.queryByText(/SOUL\.md/)).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("process-details-trigger"),
    ).not.toBeInTheDocument();

    const opened: CustomEvent[] = [];
    const handleOpen = (event: Event) => opened.push(event as CustomEvent);
    window.addEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
    const thinkingEvent = screen.getByTestId("assistant-thinking-event");
    // Thinking rows are pill-style badges (rounded-full + border + tinted
    // bg), mirroring the WorkBuddy thinking summary chip.
    expect(thinkingEvent.className).toMatch(/\brounded-full\b/);
    expect(thinkingEvent.className).toMatch(/\bborder\b/);
    expect(thinkingEvent).toHaveAttribute(
      "data-process-event-id",
      "assistant-public-summary",
    );
    fireEvent.click(thinkingEvent);
    expect(opened.at(-1)?.detail).toMatchObject({
      tab: "agent",
      eventId: "assistant-public-summary",
      eventKind: "thinking",
      view: "summary",
      processEvent: {
        kind: "thinking",
        summary: publicSummary,
        detail: publicSummary,
        status: "running",
        count: 1,
      },
    });
    window.removeEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
  });
});

describe("MessageList output summaries", () => {
  test("keeps intermediate file changes on the process lane until the turn ends", () => {
    const planChange: AIMessage = {
      id: "assistant-plan",
      type: "ai",
      content: `调研计划已写入 plan.md。${"下一步继续核查市场证据。".repeat(30)}`,
      tool_calls: [
        {
          id: "change-plan",
          name: "file_change",
          args: {
            changes: [
              {
                path: "data/workspaces/thread-1/output/final/plan.md",
                op: "create",
                diff: [
                  "--- /dev/null",
                  "+++ b/data/workspaces/thread-1/output/final/plan.md",
                  "@@ -0,0 +1,2 @@",
                  "+# 调研计划",
                  "+继续收集证据",
                ].join("\n"),
              },
            ],
          },
        },
      ],
    };
    const checkpoint: AIMessage = {
      id: "assistant-checkpoint",
      type: "ai",
      content: "我现在核查市场规模与增长预测。",
      additional_kwargs: { public_progress: true },
    };
    const activeSearch: AIMessage = {
      id: "assistant-search",
      type: "ai",
      content: "",
      tool_calls: [
        {
          id: "search-market",
          name: "web_search",
          args: { query: "智能睡眠市场规模 2025 2026" },
        },
      ],
    };
    const activeMessages: Message[] = [
      message("user-process-change", "human", "调研智能睡眠市场"),
      planChange,
      checkpoint,
      activeSearch,
    ];
    const { rerender } = renderMessageList({
      thread: mockThread({
        messages: activeMessages,
        streamingMessage: activeSearch,
        isLoading: true,
      }),
      locale: "zh-CN",
    });

    expect(screen.queryByText("已完成改动")).not.toBeInTheDocument();
    expect(screen.queryByTestId("output-change-set")).not.toBeInTheDocument();
    const processChange = screen.getByTestId("process-change-summary");
    expect(processChange.className).not.toMatch(/\b(?:rounded|border)\b/);
    expect(processChange.querySelector(".group\\/process-row")).not.toBeNull();
    expect(screen.getByText("plan.md")).toBeInTheDocument();

    const finalAnswer = message(
      "assistant-final",
      "ai",
      "调研已完成，完整结果和计划文件已经交付。",
    );
    const settledMessages = [...activeMessages, finalAnswer];
    rerender(
      messageListTree({
        thread: mockThread({ messages: settledMessages }),
      }),
    );

    expect(screen.getByText("已完成改动")).toBeInTheDocument();
    expect(screen.getByTestId("output-change-set")).toBeInTheDocument();
  });

  test("defers current-turn file summaries until streaming finishes", () => {
    const reportWithChange: AIMessage = {
      id: "assistant-report",
      type: "ai",
      content: `# NAS research report\n\n${"final report paragraph ".repeat(40)}`,
      tool_calls: [
        {
          id: "change-1",
          name: "file_change",
          args: {
            changes: [
              {
                path: "data/workspaces/thread-1/nas_market_research_plan.md",
                op: "update",
                diff: [
                  "--- a/data/workspaces/thread-1/nas_market_research_plan.md",
                  "+++ b/data/workspaces/thread-1/nas_market_research_plan.md",
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
    const messages: Message[] = [
      message("user-1", "human", "做一个 nas 调研"),
      reportWithChange,
    ];
    const loadingThread = mockThread({
      messages,
      streamingMessage: reportWithChange,
      isLoading: true,
    });

    const { rerender } = renderMessageList({
      thread: loadingThread,
      locale: "zh-CN",
    });

    expect(screen.getByText(/NAS research report/)).toBeInTheDocument();
    expect(
      screen.queryByText(/\u5df2\u7f16\u8f91 1 \u4e2a\u6587\u4ef6/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(
        "data/workspaces/thread-1/nas_market_research_plan.md",
      ),
    ).not.toBeInTheDocument();

    const settledThread = mockThread({ messages });
    rerender(messageListTree({ thread: settledThread }));

    // The completed change set appears only after streaming settles. It is a
    // review surface beneath the answer, separate from tool/thinking rows.
    expect(screen.getByText("已完成改动")).toBeInTheDocument();
    expect(
      screen.getByText(/\u5df2\u7f16\u8f91 1 \u4e2a\u6587\u4ef6/),
    ).toBeInTheDocument();
    expect(screen.getByTestId("output-change-set")).toBeInTheDocument();
    expect(
      screen.getByText("data/workspaces/thread-1/nas_market_research_plan.md"),
    ).toBeInTheDocument();
    const changeSet = screen.getByTestId("output-change-set");
    const feedbackAction = screen.getByRole("button", { name: "好的回复" });
    expect(
      changeSet.compareDocumentPosition(feedbackAction) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});

describe("MessageList stalled-run warning", () => {
  test("renders verification-gate failures as an inline actionable banner", () => {
    const thread = mockThread({
      messages: [
        message("user-1", "human", "Edit the code"),
        message("assistant-1", "ai", "Updated the file."),
      ],
      error: new Error(
        "Code changes were produced but no verification step was recorded before final answer.",
      ),
    });

    renderMessageList({ thread });

    expect(
      screen.getByText(/Code changes need verification before EchoAI/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/This reply was interrupted/i),
    ).not.toBeInTheDocument();
  });

  test("keeps a quiet interruption receipt inside the existing process group", () => {
    const thread = mockThread({
      messages: [
        message("user-1", "human", "Inspect the runtime"),
        {
          id: "assistant-1",
          type: "ai",
          content: "",
          additional_kwargs: {
            response_state: "interrupted",
            interrupted_draft: 'str = ""',
          },
          tool_calls: [
            {
              id: "read-1",
              name: "read_file",
              args: { path: "runtime/protocol/items.py" },
            },
          ],
        } as AIMessage,
      ],
    });

    renderMessageList({ thread });

    expect(
      screen.getByText(/This response was interrupted during generation/i),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("process-interrupted-receipt"),
    ).toBeInTheDocument();
    expect(screen.queryByText('str = ""')).not.toBeInTheDocument();
  });

  test("shows the agent avatar when a turn is interrupted before first token", () => {
    const thread = mockThread({
      messages: [
        message("user-1", "human", "Research the market"),
        {
          id: "turn-1-interrupted-receipt",
          type: "ai",
          content: "",
          additional_kwargs: {
            response_state: "interrupted",
            interrupt_reason: "connection closed",
          },
        } as AIMessage,
      ],
    });

    renderMessageList({
      thread,
      currentAgent: {
        name: "general",
        display_name: "Eve",
        avatar_url: "/api/agents/general/avatar",
      },
    });

    expect(screen.getByAltText("Eve")).toBeInTheDocument();
    expect(
      screen.getByText(/This response was interrupted during generation/i),
    ).toBeInTheDocument();
  });

  test("keeps late process callbacks above the interrupted terminal receipt", () => {
    const agentMetadata = {
      agent_id: "general",
      agent_display_name: "Eve",
    };
    const thread = mockThread({
      messages: [
        message("user-1", "human", "Inspect the runtime"),
        {
          id: "assistant-progress",
          type: "ai",
          content: "Starting the inspection.",
          additional_kwargs: {
            public_progress: true,
            ...agentMetadata,
          },
        } as AIMessage,
        {
          id: "assistant-interrupted",
          type: "ai",
          content: "",
          additional_kwargs: {
            response_state: "interrupted",
            ...agentMetadata,
          },
        } as AIMessage,
        {
          id: "assistant-late-tool",
          type: "ai",
          content: "",
          additional_kwargs: agentMetadata,
          tool_calls: [
            {
              id: "read-late",
              name: "read_file",
              args: { path: "runtime/late.ts" },
            },
          ],
        } as AIMessage,
        {
          id: "tool-late",
          type: "tool",
          content: "late result",
          tool_call_id: "read-late",
        },
      ],
    });

    renderMessageList({
      thread,
      currentAgent: {
        name: "general",
        display_name: "Eve",
        avatar_url: "/api/agents/general/avatar",
      },
    });

    const execution = screen.getByTestId("process-timeline-event-execution");
    const receipt = screen.getByText(
      /This response was interrupted during generation/i,
    );
    expect(execution).toHaveTextContent("late.ts");
    expect(
      execution.compareDocumentPosition(receipt) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.getAllByAltText("Eve")).toHaveLength(1);
  });

  test("does not leave an orphan assistant avatar for hidden auto-verification steps", () => {
    const thread = mockThread({
      messages: [
        message("user-1", "human", "continue"),
        {
          id: "ai-verify",
          type: "ai",
          content: "",
          tool_calls: [
            {
              id: "verify-1",
              name: "verification",
              args: {},
            },
          ],
        } as AIMessage,
        {
          id: "tool-verify-1",
          type: "tool",
          content: "verification required",
          tool_call_id: "verify-1",
        },
      ],
    });

    renderMessageList({
      thread,
      currentAgent: {
        name: "general",
        display_name: "Echo",
        avatar_url: "/api/agents/general/avatar",
        icon: null,
      },
    });

    expect(
      screen.queryByText(/verification required/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByAltText("Echo")).not.toBeInTheDocument();
  });

  test("does not leave an orphan assistant avatar for leaked control-only text", () => {
    const thread = mockThread({
      messages: [
        message("user-1", "human", "只读比较两个文件"),
        {
          id: "ai-control-only",
          type: "ai",
          content: "<read_only> </read_only>",
          additional_kwargs: {
            agent_id: "general",
            agent_display_name: "Echo",
          },
        } as AIMessage,
      ],
    });

    renderMessageList({
      thread,
      currentAgent: {
        name: "general",
        display_name: "Echo",
        avatar_url: "/api/agents/general/avatar",
        icon: null,
      },
    });

    expect(screen.queryByText(/read_only/i)).not.toBeInTheDocument();
    expect(screen.queryByAltText("Echo")).not.toBeInTheDocument();
  });

  test("warns only after a loading run stops making visible progress", () => {
    vi.useFakeTimers();
    try {
      const messages = [
        message("user-1", "human", "Write a report"),
        message("assistant-1", "ai", "Planning"),
      ];
      const thread = mockThread({
        messages,
        streamingMessage: messages[1],
        isLoading: true,
      });

      renderMessageList({ thread });

      act(() => {
        vi.advanceTimersByTime(MESSAGE_LIST_TIMEOUT_WARNING_MS - 1_000);
      });
      expect(screen.queryByText(/No progress for/)).not.toBeInTheDocument();

      act(() => {
        vi.advanceTimersByTime(2_000);
      });
      expect(screen.getByText(/No progress for/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  test("resets the warning timer when streaming content advances", () => {
    vi.useFakeTimers();
    try {
      // Reduced-motion disables the body typewriter (which would otherwise
      // register its own setInterval and pollute the spy count below).
      vi.mocked(window.matchMedia).mockImplementation(
        (query: string) =>
          ({
            matches: true,
            media: query,
            onchange: null,
            addListener: vi.fn(),
            removeListener: vi.fn(),
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
            dispatchEvent: vi.fn(),
          }) as MediaQueryList,
      );
      const intervalSpy = vi.spyOn(window, "setInterval");
      const firstMessages = [
        message("user-1", "human", "Write a report"),
        message("assistant-1", "ai", "Planning"),
      ];
      const firstThread = mockThread({
        messages: firstMessages,
        streamingMessage: firstMessages[1],
        isLoading: true,
      });
      const { rerender } = renderMessageList({ thread: firstThread });

      act(() => {
        vi.advanceTimersByTime(60_000);
      });

      const nextMessages = [
        firstMessages[0]!,
        message("assistant-1", "ai", "Planning\nCollecting sources"),
      ];
      const nextThread = mockThread({
        messages: nextMessages,
        streamingMessage: nextMessages[1],
        isLoading: true,
      });

      rerender(messageListTree({ thread: nextThread }));

      expect(intervalSpy).toHaveBeenCalledTimes(1);

      act(() => {
        vi.advanceTimersByTime(60_000);
      });
      expect(screen.queryByText(/No progress for/)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  test("streams the body through the typewriter buffer while loading", () => {
    vi.useFakeTimers();
    try {
      // Typewriter enabled (reduced-motion off).
      vi.mocked(window.matchMedia).mockImplementation(
        (query: string) =>
          ({
            matches: false,
            media: query,
            onchange: null,
            addListener: vi.fn(),
            removeListener: vi.fn(),
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
            dispatchEvent: vi.fn(),
          }) as MediaQueryList,
      );

      const user = message("user-1", "human", "hi");
      const partial = message("assistant-1", "ai", "Plan");
      const firstThread = mockThread({
        messages: [user, partial],
        streamingMessage: partial,
        isLoading: true,
      });
      const { rerender } = renderMessageList({ thread: firstThread });

      // The body grows while streaming; the buffer plays back the delta.
      const grown = message(
        "assistant-1",
        "ai",
        "Planning and collecting sources",
      );
      const grownThread = mockThread({
        messages: [user, grown],
        streamingMessage: grown,
        isLoading: true,
      });
      rerender(messageListTree({ thread: grownThread }));

      // The full text is NOT visible yet — the buffer is mid-playback.
      expect(
        screen.queryByText("Planning and collecting sources"),
      ).not.toBeInTheDocument();

      // Advancing the ticker drains the buffer to the full body.
      act(() => {
        vi.advanceTimersByTime(60_000);
      });
      expect(
        screen.getByText("Planning and collecting sources"),
      ).toBeInTheDocument();

      // Stream settles: full text shown immediately, no lingering buffer.
      const settledThread = mockThread({
        messages: [user, grown],
      });
      rerender(messageListTree({ thread: settledThread }));
      expect(
        screen.getByText("Planning and collecting sources"),
      ).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  test("environment-blocked banner offers network authorization", () => {
    const user = message("user-env", "human", "跑一下测试");
    const failed: AIMessage = {
      id: "assistant-env",
      type: "ai",
      content: "我已完成代码修改。",
      additional_kwargs: {
        agent_id: "general",
        agent_display_name: "Eve",
        error: {
          message: "验证命令因环境受限未能执行 (网络不可用)",
          info: {
            failure_kind: "environment",
            disposition: "failed",
            code: "environment_blocked",
          },
        },
      },
    };
    const onAuthorizeNetwork = vi.fn();
    renderMessageList({
      thread: mockThread({
        messages: [user, failed],
        error: new Error("turn failed"),
      }),
      mode: "code",
      locale: "zh-CN",
      onAuthorizeNetwork,
    });

    const commonButton = screen.getByText("授权「常用域名」并重试");
    const fullButton = screen.getByText("授权完整网络并重试");
    expect(commonButton).toBeInTheDocument();
    expect(fullButton).toBeInTheDocument();

    fireEvent.click(commonButton);
    expect(onAuthorizeNetwork).toHaveBeenCalledWith("common");
    fireEvent.click(fullButton);
    expect(onAuthorizeNetwork).toHaveBeenCalledWith("full");
  });
});
