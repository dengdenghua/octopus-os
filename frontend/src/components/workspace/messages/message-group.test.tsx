import { act, fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AIMessage, Message } from "@/core/api/types";
import { renderWithProviders } from "@/test/harness";

import { groupMessages } from "@/core/messages/utils";

import { AGENT_WORKBENCH_OPEN_EVENT } from "../agent-workbench-events";
import {
  hasVisibleMessageGroupContent,
  MessageGroup,
  convertToSteps,
  getLiveStreamWindowHeight,
  groupConsecutiveReasoningSteps,
  selectCompactTimelineItems,
  type TimelineItem,
} from "./message-group";

// jsdom has no matchMedia. Simulate "prefers-reduced-motion: reduce" so the
// streaming text buffer reveals full text instantly in tests (typewriter
// timing is covered separately in use-streaming-text-buffer.test.ts).
beforeEach(() => {
  window.matchMedia = vi.fn().mockReturnValue({
    matches: true,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }) as unknown as typeof window.matchMedia;
});

vi.mock("../artifacts", () => ({
  useArtifacts: () => ({
    setOpen: vi.fn(),
    autoOpen: false,
    autoSelect: false,
    selectedArtifact: null,
    select: vi.fn(),
  }),
}));

describe("MessageGroup todo_write rendering", () => {
  it("keeps short live output compact and caps long output", () => {
    expect(getLiveStreamWindowHeight(36)).toBe(36);
    expect(getLiveStreamWindowHeight(127.2)).toBe(128);
    expect(getLiveStreamWindowHeight(260)).toBe(128);
  });

  it("keeps same-phase reasoning on both sides of a tool in causal order", () => {
    const items = groupConsecutiveReasoningSteps([
      {
        id: "reason-before",
        type: "reasoning",
        reasoning: "先检查配置",
        phaseId: "inspect",
      },
      {
        id: "read-config",
        type: "toolCall",
        name: "read_file",
        args: { path: "config.ts" },
        phaseId: "inspect",
      },
      {
        id: "reason-after",
        type: "reasoning",
        reasoning: "配置读取完成，继续核对引用",
        phaseId: "inspect",
      },
    ]);

    expect(items.map((item) => item.type)).toEqual([
      "reasoningGroup",
      "toolCall",
      "reasoningGroup",
    ]);
  });

  it("removes recovery handoff text from public timeline steps", () => {
    const steps = convertToSteps([
      {
        id: "ai-recovery",
        type: "ai",
        content: "",
        additional_kwargs: {
          public_reasoning_summary:
            "所有文件读取任务已完成。Resume state: 继续核对剩余证据。",
        },
      } as AIMessage,
    ]);
    expect(
      steps.map((step) => ("reasoning" in step ? step.reasoning : "")),
    ).toEqual(["所有文件读取任务已完成。"]);
  });

  it("attributes reasoning_duration_ms once per message, not per split chunk", () => {
    // A single AI message can carry a long reasoning stream that is cut into
    // several chunks. reasoning_duration_ms is the TOTAL for the whole
    // message; it must be attached to the first chunk only, otherwise every
    // chunk (and any merged group) over-counts the thinking time by the
    // chunk count — e.g. many consecutive "思考了 38.7秒" blocks.
    const message: AIMessage = {
      id: "ai-think-1",
      type: "ai",
      content: "",
      additional_kwargs: {
        reasoning_content: "第一段思考。\n\n第二段思考。\n\n第三段思考。",
        reasoning_duration_ms: 38700,
      },
    };
    const steps = convertToSteps([message]);
    const reasoningSteps = steps.filter(
      (step): step is TimelineItem & { reasoning: string } =>
        "reasoning" in step,
    );
    expect(reasoningSteps.length).toBeGreaterThan(1);
    const durations = reasoningSteps.map((step) => step.durationMs);
    expect(durations[0]).toBe(38700);
    for (let index = 1; index < durations.length; index += 1) {
      expect(durations[index]).toBeUndefined();
    }
  });

  it("hides todo_write tool calls from the execution timeline", () => {
    const message: AIMessage = {
      id: "ai-1",
      type: "ai",
      content: "",
      tool_calls: [
        {
          id: "todo-1",
          name: "todo_write",
          args: {
            todos: JSON.stringify([
              { text: "Confirm task", status: "completed" },
              {
                text: "Check constraints",
                status: "in_progress",
                active_form: "Checking constraints",
              },
              { text: "Output result", status: "pending" },
            ]),
          },
        },
      ],
    };

    renderWithProviders(<MessageGroup messages={[message]} />, {
      locale: "en-US",
    });

    expect(screen.queryByText("Update to-do list")).not.toBeInTheDocument();
    expect(screen.queryByText("Task plan")).not.toBeInTheDocument();
    expect(screen.queryByText("Confirm task")).not.toBeInTheDocument();
    expect(screen.queryByText("Checking constraints")).not.toBeInTheDocument();
    expect(screen.queryByText("Output result")).not.toBeInTheDocument();
    expect(screen.queryByText(/todo_write/)).not.toBeInTheDocument();
  });

  it("hides internal blackboard writes from the public execution timeline", () => {
    const message: AIMessage = {
      id: "ai-blackboard",
      type: "ai",
      content: "",
      tool_calls: [
        {
          id: "bb-1",
          name: "bb_write",
          args: {
            key: "internal.progress",
            value: "machine-only coordination state",
          },
        },
      ],
    };

    renderWithProviders(<MessageGroup messages={[message]} />, {
      locale: "en-US",
    });

    expect(screen.queryByText(/bb_write/i)).not.toBeInTheDocument();
    expect(
      screen.queryByText("machine-only coordination state"),
    ).not.toBeInTheDocument();
  });

  it("hides auto verification tool calls from restored history", () => {
    const messages: Message[] = [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "verify-1",
            name: "verification",
            args: {},
          },
          {
            id: "read-1",
            name: "read_file",
            args: { path: "notes.md" },
          },
        ],
      },
      {
        id: "tool-verify-1",
        type: "tool",
        content: "verification required",
        tool_call_id: "verify-1",
      },
    ];

    renderWithProviders(<MessageGroup messages={messages} />, {
      locale: "en-US",
    });

    expect(screen.queryByText(/verification/i)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/verification required/i),
    ).not.toBeInTheDocument();

    const opened: CustomEvent[] = [];
    const handleOpen = (event: Event) => opened.push(event as CustomEvent);
    window.addEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
    expect(
      screen.queryByTestId("process-details-trigger"),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("process-timeline-event-execution"));

    const processEvent = opened.at(-1)?.detail.processEvent;
    expect(processEvent?.summary).not.toBe("Open details");
    expect(processEvent?.summary).toMatch(/notes\.md/);
    expect(processEvent?.detail).toMatch(/notes\.md/);
    expect(screen.queryByText(/verification/i)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/verification required/i),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/notes\.md/)).toBeInTheDocument();
    window.removeEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
  });

  it("treats auto-verification-only groups as empty", () => {
    const messages: Message[] = [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "verify-1",
            name: "verification",
            args: {},
          },
        ],
      },
      {
        id: "tool-verify-1",
        type: "tool",
        content: "verification required",
        tool_call_id: "verify-1",
      },
    ];

    expect(hasVisibleMessageGroupContent(messages)).toBe(false);

    const { container } = renderWithProviders(
      <MessageGroup messages={messages} />,
      {
        locale: "en-US",
      },
    );

    expect(container).toBeEmptyDOMElement();
  });
});

describe("MessageGroup reasoning grouping", () => {
  it("keeps reasoning on both sides of a tool in separate causal disclosures", () => {
    const messages: AIMessage[] = [
      {
        id: "native-thinking",
        type: "ai",
        content: "",
        additional_kwargs: {
          reasoning_content: "深度思考摘要",
          reasoning_duration_ms: 22_800,
          phase_id: "research-phase",
        },
      },
      {
        id: "read-readme",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "read-readme-call",
            name: "read_file",
            args: { path: "README.md" },
          },
        ],
      },
      {
        id: "raw-thinking",
        type: "ai",
        content: "",
        additional_kwargs: {
          reasoning_content: "我需要继续深入，当前阶段信息还不足够。",
          phase_id: "research-phase",
        },
      },
    ];

    renderWithProviders(<MessageGroup messages={messages} />, {
      locale: "zh-CN",
    });

    expect(
      screen.getAllByTestId("process-timeline-event-thinking"),
    ).toHaveLength(2);
    expect(screen.getByText("README.md")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("thinking-row-toggle"));
    expect(screen.getByTestId("thinking-row-content")).toHaveTextContent(
      "深度思考摘要",
    );
  });

  it("keeps reasoning from different structured phases separate", () => {
    const messages: AIMessage[] = [
      {
        id: "phase-a-thinking",
        type: "ai",
        content: "",
        additional_kwargs: {
          reasoning_content: "第一阶段思考",
          reasoning_duration_ms: 12_000,
          phase_id: "phase-a",
        },
      },
      {
        id: "phase-b-thinking",
        type: "ai",
        content: "",
        additional_kwargs: {
          reasoning_content: "第二阶段思考",
          reasoning_duration_ms: 13_000,
          phase_id: "phase-b",
        },
      },
      {
        id: "phase-tool",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "phase-tool-call",
            name: "web_search",
            args: { query: "phase boundary" },
          },
        ],
      },
    ];

    renderWithProviders(<MessageGroup messages={messages} />, {
      locale: "zh-CN",
    });

    expect(
      screen.getAllByTestId("process-timeline-event-thinking"),
    ).toHaveLength(2);
  });

  it("keeps the earlier disclosure open when later same-phase reasoning follows a tool", () => {
    const first: AIMessage = {
      id: "stable-thinking-first",
      type: "ai",
      content: "",
      additional_kwargs: {
        reasoning_content: "先核对第一批证据。",
        reasoning_duration_ms: 11_000,
        phase_id: "stable-phase",
      },
    };
    const nextMessages: AIMessage[] = [
      first,
      {
        id: "stable-search",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "stable-search-call",
            name: "web_search",
            args: { query: "additional evidence" },
          },
        ],
      },
      {
        id: "stable-thinking-second",
        type: "ai",
        content: "",
        additional_kwargs: {
          reasoning_content: "再核对第二批证据。",
          reasoning_duration_ms: 9_000,
          phase_id: "stable-phase",
        },
      },
    ];

    const { rerender } = renderWithProviders(
      <MessageGroup messages={[first]} />,
      { locale: "zh-CN" },
    );
    fireEvent.click(screen.getByTestId("thinking-row-toggle"));
    expect(screen.getByTestId("thinking-row-content")).toHaveAttribute(
      "data-state",
      "open",
    );

    rerender(<MessageGroup messages={nextMessages} />);

    expect(
      screen.getAllByTestId("process-timeline-event-thinking"),
    ).toHaveLength(2);
    expect(screen.getByTestId("thinking-row-content")).toHaveAttribute(
      "data-state",
      "open",
    );
  });

  it("does not echo a public checkpoint as thinking when it carries completed tools", () => {
    const paths = ["a.py", "b.ts", "c.ts", "d.ts"];
    const messages: AIMessage[] = [
      {
        id: "coverage-progress",
        type: "ai",
        content: "四个目标文件均已读取完毕，关键字段一致；下一步整理最终结论。",
        additional_kwargs: {
          public_progress: true,
          progress_sequence: 1,
          timeline_sequence: 5,
        },
        tool_calls: paths.map((path, index) => ({
          id: `read-${index}`,
          name: "read_file",
          args: { path },
          timelineSequence: index + 1,
          type: "tool_call" as const,
        })),
      },
    ];

    renderWithProviders(<MessageGroup messages={messages} />, {
      locale: "zh-CN",
    });

    expect(screen.getAllByTestId("public-progress-event")).toHaveLength(1);
    const visibleExecution = screen.getByTestId(
      "process-timeline-event-execution",
    );
    expect(visibleExecution).toHaveAttribute("data-process-event-id", "read-3");
    expect(
      screen.queryByTestId("process-timeline-event-thinking"),
    ).not.toBeInTheDocument();

    const opened: CustomEvent[] = [];
    const handleOpen = (event: Event) => opened.push(event as CustomEvent);
    window.addEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
    expect(
      screen.queryByTestId("process-details-trigger"),
    ).not.toBeInTheDocument();
    fireEvent.click(visibleExecution);
    expect(opened.at(-1)?.detail).toMatchObject({
      eventKind: "execution",
      view: "trace",
      processEvent: { count: 4 },
    });
    for (const path of paths) {
      expect(opened.at(-1)?.detail.processEvent.detail).toContain(path);
    }
    expect(screen.queryByText("a.py")).not.toBeInTheDocument();
    window.removeEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
  });

  it("renders identical public summary and checkpoint text only once", () => {
    const checkpoint = "正在读取事件适配器与消息组件，核对时间线顺序。";
    const messages: AIMessage[] = [
      {
        id: "progress-with-summary",
        type: "ai",
        content: checkpoint,
        additional_kwargs: {
          public_progress: true,
          public_reasoning_summary: checkpoint,
        },
        tool_calls: [
          {
            id: "read-adapter",
            name: "read_file",
            args: { path: "realtime-adapter.ts" },
          },
        ],
      },
    ];

    renderWithProviders(<MessageGroup messages={messages} isLoading />, {
      locale: "zh-CN",
    });

    expect(screen.getAllByText(checkpoint)).toHaveLength(1);
    expect(screen.getAllByTestId("public-progress-event")).toHaveLength(1);
    expect(
      screen.queryByTestId("process-timeline-event-thinking"),
    ).not.toBeInTheDocument();
  });

  it("renders public checkpoints inline between thinking and execution", () => {
    const messages: AIMessage[] = [
      {
        id: "progress-1",
        type: "ai",
        content: "已确认流事件按消息、思考和执行三条通道归一化。",
        additional_kwargs: {
          public_progress: true,
          reasoning_content: "inspect the bridge",
          grounding: [
            {
              kind: "source",
              title: "realtime_event_bridge.py",
              path: "runtime/sensing/gateway/realtime_event_bridge.py",
            },
          ],
        },
      },
      {
        id: "progress-2",
        type: "ai",
        content: "进一步确认执行完成后才会开启下一轮公开结论。",
        additional_kwargs: {
          public_progress: true,
          phase_id: "turn-1:progress:2",
          parent_item_id: "read-bridge",
          progress_sequence: 2,
          timeline_sequence: 3,
          reasoning_content: "inspect the reducer",
        },
        tool_calls: [
          {
            id: "read-bridge",
            name: "read_file",
            args: { path: "realtime_event_bridge.py" },
            timelineSequence: 2,
            parentItemId: "progress-1",
            phaseId: "turn-1:progress:1",
          },
        ],
      },
    ];

    const groups = groupMessages(messages, (group) => group);
    expect(groups).toHaveLength(1);
    expect(groups[0]?.type).toBe("assistant:processing");

    renderWithProviders(<MessageGroup messages={messages} isLoading />, {
      locale: "zh-CN",
    });

    const checkpoints = screen.getAllByTestId("public-progress-event");
    expect(checkpoints).toHaveLength(2);
    expect(checkpoints[0]).toHaveClass(
      "narrative-progress-row",
      "text-foreground",
    );
    expect(checkpoints[0]).not.toHaveClass("text-muted-foreground");
    expect(checkpoints[1]).toHaveAttribute(
      "data-phase-id",
      "turn-1:progress:2",
    );
    expect(checkpoints[1]).toHaveAttribute(
      "data-parent-item-id",
      "read-bridge",
    );
    expect(checkpoints[1]).toHaveAttribute("data-progress-sequence", "2");
    expect(checkpoints[1]).toHaveAttribute("data-timeline-sequence", "3");
    const opened: CustomEvent[] = [];
    const handleOpen = (event: Event) => opened.push(event as CustomEvent);
    window.addEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
    fireEvent.click(checkpoints[0]!);
    expect(opened.at(-1)?.detail).toMatchObject({
      eventId: "progress-1",
      eventKind: "thinking",
      view: "summary",
      processEvent: {
        kind: "thinking",
        detail: "已确认流事件按消息、思考和执行三条通道归一化。",
        status: "done",
        count: 1,
      },
    });
    window.removeEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
    const groundingTrigger = screen.getByRole("button", {
      name: "参考 realtime_event_bridge.py",
    });
    expect(checkpoints[0]).toContainElement(groundingTrigger);
    expect(screen.queryByText("定向")).not.toBeInTheDocument();
    expect(screen.queryByText("验证")).not.toBeInTheDocument();
    expect(
      screen.getByText("已确认流事件按消息、思考和执行三条通道归一化。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("进一步确认执行完成后才会开启下一轮公开结论。"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("process-timeline-event-execution"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("process-timeline-event-execution"),
    ).toHaveAttribute("data-timeline-sequence", "2");
    expect(
      screen.getByTestId("process-timeline-event-execution"),
    ).not.toHaveTextContent(/^执行(?:\s|·)/);
  });

  it("keeps representative public progress while bounding a long run", () => {
    const updates = [
      "先确认时间线的数据来源。",
      "第一次读取失败，正在切换路径。",
      "第二次读取仍失败，继续尝试。",
      "已经找到真实仓库位置，开始核对适配层。",
      "适配层已确认，继续检查渲染层。",
      "渲染层证据已齐，准备收束。",
      "全部证据已经完成。",
    ];
    const messages: AIMessage[] = updates.map((content, index) => ({
      id: `progress-${index + 1}`,
      type: "ai",
      content,
      additional_kwargs: {
        public_progress: true,
        progress_sequence: index + 1,
        timeline_sequence: index + 1,
      },
    }));

    renderWithProviders(<MessageGroup messages={messages} isLoading />, {
      locale: "zh-CN",
    });

    expect(screen.getAllByTestId("public-progress-event")).toHaveLength(4);
    expect(screen.getByText(updates[0]!)).toBeInTheDocument();
    expect(screen.getByText(updates[1]!)).toBeInTheDocument();
    expect(screen.getByText(updates[4]!)).toBeInTheDocument();
    expect(screen.getByText(updates[6]!)).toBeInTheDocument();
    expect(screen.queryByText(updates[2]!)).not.toBeInTheDocument();
    expect(screen.queryByText(updates[3]!)).not.toBeInTheDocument();
    expect(screen.queryByText(updates[5]!)).not.toBeInTheDocument();
  });

  it("deduplicates replayed checkpoints and tool ids in the main transcript", () => {
    const repeatedProgress = "正在读取消息组件，确认时间线的真实渲染顺序。";
    const messages: AIMessage[] = ["progress-a", "progress-b"].map((id) => ({
      id,
      type: "ai",
      content: repeatedProgress,
      additional_kwargs: { public_progress: true },
      tool_calls: [
        {
          id: "read-shared",
          name: "read_file",
          args: { path: "message-group.tsx" },
        },
      ],
    }));

    renderWithProviders(<MessageGroup messages={messages} />, {
      locale: "zh-CN",
    });

    expect(screen.getAllByTestId("public-progress-event")).toHaveLength(1);
    expect(
      screen.getAllByTestId("process-timeline-event-execution"),
    ).toHaveLength(1);
    expect(screen.getAllByText(repeatedProgress)).toHaveLength(1);
  });

  it("keeps legacy reasoning summaries compact and private while streaming", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        additional_kwargs: { public_reasoning_summary: "先扫一遍上下文" },
      },
      {
        id: "ai-2",
        type: "ai",
        content: "",
        additional_kwargs: {
          public_reasoning_summary: "再整理成可执行步骤",
        },
      },
    ];

    renderWithProviders(
      <MessageGroup messages={messages as never} isLoading />,
      {
        locale: "zh-CN",
      },
    );

    const thinkingEvent = screen.getByTestId("process-timeline-event-thinking");
    expect(thinkingEvent).toBeInTheDocument();
    expect(thinkingEvent).not.toHaveTextContent(/^思考过程(?:\s|·)/);
    expect(
      screen.queryByTestId("process-details-trigger"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("live-thinking-stream")).toHaveTextContent(
      "再整理成可执行步骤",
    );
    expect(thinkingEvent).not.toHaveTextContent("先扫一遍上下文");
    expect(thinkingEvent).toHaveClass(
      "text-[13px]",
      "text-muted-foreground/75",
      "min-h-7",
    );
    expect(thinkingEvent).not.toHaveClass("narrative-progress-row");
    expect(screen.queryByText("01")).not.toBeInTheDocument();
    expect(screen.queryByText("02")).not.toBeInTheDocument();

    const opened: CustomEvent[] = [];
    const handleOpen = (event: Event) => opened.push(event as CustomEvent);
    window.addEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
    fireEvent.click(thinkingEvent);

    expect(thinkingEvent).not.toHaveTextContent("先扫一遍上下文");
    expect(opened.at(-1)?.detail).toMatchObject({
      eventKind: "thinking",
      view: "summary",
      processEvent: { count: 2 },
    });
    expect(opened.at(-1)?.detail.processEvent.detail).toContain(
      "先扫一遍上下文",
    );
    expect(screen.getByTestId("live-thinking-stream")).toHaveTextContent(
      "再整理成可执行步骤",
    );
    window.removeEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
  });

  it("shows a typewriter window only for private reasoning, then folds after settle", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        additional_kwargs: { reasoning_content: "Evaluate directory evidence" },
      },
      {
        id: "ai-2",
        type: "ai",
        content: "",
        additional_kwargs: {
          reasoning_content: "Compare configuration evidence",
        },
      },
    ];

    const { rerender } = renderWithProviders(
      <MessageGroup messages={messages as never} isLoading />,
      { locale: "zh-CN" },
    );

    // While streaming: the live window carries the latest thought's full
    // text; the truncated row summary is hidden so text isn't duplicated.
    const stream = screen.getByTestId("live-thinking-stream");
    expect(stream).toHaveClass("live-thinking-window", "ml-4");
    expect(stream).toHaveClass("max-h-32");
    expect(stream).not.toHaveClass("h-32");
    expect(stream).toHaveAttribute(
      "data-height-policy",
      "inner-content-capped-history-frozen",
    );
    expect(stream).toHaveTextContent("Compare configuration evidence");
    expect(screen.getAllByText("Compare configuration evidence")).toHaveLength(
      1,
    );

    // After the stream settles, private reasoning returns to its compact row.
    rerender(<MessageGroup messages={messages as never} />);
    expect(
      screen.queryByTestId("live-thinking-stream"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByTestId("process-timeline-event-thinking"),
    ).toHaveTextContent("Compare configuration evidence");
  });

  it("caps long live thinking without forcing short thoughts to the cap", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-live-thinking-scroll",
        type: "ai",
        content: "",
        additional_kwargs: {
          reasoning_content: "持续生成的深度思考内容".repeat(80),
        },
      },
    ];

    renderWithProviders(
      <MessageGroup messages={messages as never} isLoading />,
      { locale: "zh-CN" },
    );

    // 长思考不能无限把正文顶下去；短思考也不能被强制撑到 128px。
    const stream = screen.getByTestId("live-thinking-stream");
    expect(stream).toHaveClass("overflow-y-auto", "max-h-32");
    expect(stream).not.toHaveClass("h-32");
    expect(stream).toHaveTextContent("持续生成的深度思考内容");
  });

  it("shrinks a capped live window when its content becomes shorter", () => {
    const scrollHeight = vi
      .spyOn(HTMLElement.prototype, "scrollHeight", "get")
      .mockImplementation(function (this: HTMLElement) {
        if (this.dataset.testid === "live-thinking-content") {
          return (this.textContent?.length ?? 0) > 40 ? 260 : 36;
        }
        return 0;
      });
    const longMessage: AIMessage = {
      id: "ai-live-thinking-shrink",
      type: "ai",
      content: "",
      additional_kwargs: {
        reasoning_content: "很长的实时推理内容".repeat(40),
      },
    };
    const shortMessage: AIMessage = {
      ...longMessage,
      additional_kwargs: { reasoning_content: "简短结论" },
    };

    try {
      const { rerender } = renderWithProviders(
        <MessageGroup messages={[longMessage] as never} isLoading />,
        { locale: "zh-CN" },
      );
      expect(screen.getByTestId("live-thinking-stream")).toHaveStyle({
        height: "128px",
      });

      rerender(<MessageGroup messages={[shortMessage] as never} isLoading />);
      expect(screen.getByTestId("live-thinking-stream")).toHaveStyle({
        height: "36px",
      });
    } finally {
      scrollHeight.mockRestore();
    }
  });

  it("streams thinking expanded, then collapses it once the turn settles", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-thinking-lifecycle",
        type: "ai",
        content: "",
        additional_kwargs: {
          reasoning_content: "逐步核对证据链".repeat(40),
        },
      },
    ];

    const { rerender } = renderWithProviders(
      <MessageGroup messages={messages as never} isLoading />,
      { locale: "zh-CN" },
    );

    // 流式中：窗口默认展开
    expect(screen.getByTestId("live-thinking-window")).toHaveAttribute(
      "data-state",
      "open",
    );

    // 完成后：窗口收起，思考回到紧凑行，展开区默认折叠
    rerender(<MessageGroup messages={messages as never} />);
    expect(
      screen.queryByTestId("live-thinking-stream"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("thinking-row-content")).toHaveAttribute(
      "data-state",
      "closed",
    );
  });

  it("keeps wording-based internal reasoning muted, collapsed, and markdown-formatted on demand", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-internal-next-step",
        type: "ai",
        content: "",
        additional_kwargs: {
          reasoning_content: [
            "接下来：pop stash → 跑前端测试/typecheck → 审查代码 diff。",
            "",
            "我需要继续审计任务。刚才已经确认：",
            "",
            "- 基线测试同样失败，不是本次改动引入。",
            "- 工作区改动已经恢复。",
            "",
            "然后读取 `react_loop.py` 继续核对。",
          ].join("\n"),
        },
      },
    ];

    renderWithProviders(<MessageGroup messages={messages as never} />, {
      locale: "zh-CN",
    });

    const row = screen.getByTestId("process-timeline-event-thinking");
    expect(row).not.toHaveClass("narrative-progress-row", "text-foreground");
    expect(row).toHaveClass(
      "text-[13px]",
      "text-muted-foreground/75",
      "min-h-7",
    );
    expect(screen.getByTestId("thinking-row-content")).toHaveAttribute(
      "data-state",
      "closed",
    );

    fireEvent.click(screen.getByTestId("thinking-row-toggle"));

    const detail = screen.getByTestId("thinking-row-content");
    expect(detail).toHaveAttribute("data-state", "open");
    expect(row).toHaveTextContent("思考过程");
    expect(row).not.toHaveTextContent("接下来：pop stash");
    expect(detail.firstElementChild).toHaveClass("whitespace-pre-wrap");
    expect(detail).toHaveTextContent("基线测试同样失败");
    expect(detail).toHaveTextContent("工作区改动已经恢复");
    expect(detail).toHaveTextContent("react_loop.py");
  });

  it("uses the protocol lane when reasoning and commentary have identical text", () => {
    const sharedText = "接下来检查前端测试，再审查代码差异。";
    const messages: AIMessage[] = [
      {
        id: "ai-private-reasoning",
        type: "ai",
        content: "",
        additional_kwargs: {
          reasoning_content: sharedText,
        },
      },
      {
        id: "ai-public-commentary",
        type: "ai",
        content: sharedText,
        additional_kwargs: {
          message_kind: "commentary",
          public_progress: true,
        },
      },
    ];

    renderWithProviders(<MessageGroup messages={messages as never} />, {
      locale: "zh-CN",
    });

    const reasoningRow = screen.getByTestId("process-timeline-event-thinking");
    const commentaryRow = screen.getByTestId("public-progress-event");
    expect(reasoningRow).toHaveTextContent(sharedText);
    expect(reasoningRow).toHaveClass("text-[13px]", "text-muted-foreground/75");
    expect(reasoningRow).not.toHaveClass("narrative-progress-row");
    expect(commentaryRow).toHaveTextContent(sharedText);
    expect(commentaryRow).toHaveClass(
      "narrative-progress-row",
      "text-foreground",
    );
  });

  it("keeps legacy reasoning summaries compact and preserves workbench evidence", () => {
    const messages: AIMessage[] = Array.from({ length: 4 }, (_, index) => ({
      id: `ai-${index + 1}`,
      type: "ai",
      content: "",
      additional_kwargs: {
        public_reasoning_summary: `Latest trace thought ${index + 1}.`,
      },
    }));

    renderWithProviders(
      <MessageGroup messages={messages as never} keepOpen />,
      {
        locale: "en-US",
      },
    );

    expect(
      screen.queryByTestId("process-details-trigger"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("01")).not.toBeInTheDocument();
    expect(screen.queryByText("03")).not.toBeInTheDocument();
    const reasoningTrace = screen.getByTestId(
      "process-timeline-event-thinking",
    );
    expect(reasoningTrace).not.toHaveTextContent("Latest trace thought 1.");
    expect(reasoningTrace).toHaveTextContent("Latest trace thought 4.");
    expect(reasoningTrace).toHaveClass("text-muted-foreground/75");

    const opened: CustomEvent[] = [];
    const handleOpen = (event: Event) => opened.push(event as CustomEvent);
    window.addEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
    fireEvent.click(reasoningTrace);

    expect(reasoningTrace).not.toHaveTextContent("Latest trace thought 1.");
    expect(opened.at(-1)?.detail.processEvent.detail).toContain(
      "Latest trace thought 1.",
    );
    expect(opened.at(-1)?.detail.processEvent.count).toBe(4);
    window.removeEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
  });

  it("keeps compact process rows visually quiet instead of card-like", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-quiet-expanded-1",
        type: "ai",
        content: "",
        additional_kwargs: {
          public_reasoning_summary:
            "第一段足够长，需要展开才能看到完整内容。\n继续说明这一段的细节。",
        },
      },
      {
        id: "ai-quiet-expanded-2",
        type: "ai",
        content: "",
        additional_kwargs: {
          public_reasoning_summary:
            "第二段也足够长，需要展开才能看到完整内容。\n继续说明这一段的细节。",
        },
      },
    ];

    const { container } = renderWithProviders(
      <MessageGroup messages={messages} keepOpen />,
      {
        locale: "zh-CN",
      },
    );

    const row = screen.getByTestId("process-timeline-event-thinking");
    expect(row).not.toHaveClass("rounded-md");
    expect(row).not.toHaveClass("border");
    expect(row).not.toHaveClass("bg-muted");
    expect(row).not.toHaveClass("shadow");
    expect(container.querySelector("[data-cot-connector='true']")).toBeNull();
  });

  it("never turns private reasoning tool protocol into public actions", () => {
    const message: AIMessage = {
      id: "ai-search",
      type: "ai",
      content: "",
      additional_kwargs: {
        reasoning_content: [
          "Let me search for more specific data on this.",
          "<tool_call><function=web_search><parameter=query>AI Agent SMB opportunity</parameter></function></tool_call>",
          "The search results are coming back empty for many of my queries.",
        ].join("\n\n"),
      },
    };

    renderWithProviders(<MessageGroup messages={[message]} keepOpen />, {
      locale: "en-US",
    });

    expect(
      screen.queryByTestId("process-timeline-event-execution"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Thought")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Let me search for more specific data on this."),
    ).not.toBeInTheDocument();

    expect(screen.queryByTitle(/Replay/)).not.toBeInTheDocument();
    expect(
      screen.queryByText("Search sources: AI Agent SMB opportunity"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/web_search/)).not.toBeInTheDocument();
  });

  it("keeps long reasoning compact and sends full evidence to the workbench", () => {
    const hiddenTail = "UNIQUE_NESTED_REASONING_TAIL";
    const messages: AIMessage[] = [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        additional_kwargs: {
          public_reasoning_summary: `First I will inspect the request and summarize the path before touching the UI. ${"extra context ".repeat(24)} ${hiddenTail}`,
        },
      },
      {
        id: "ai-2",
        type: "ai",
        content: "",
        additional_kwargs: {
          public_reasoning_summary:
            "Second I will choose the next interface step.",
        },
      },
    ];

    renderWithProviders(<MessageGroup messages={messages as never} />, {
      locale: "en-US",
    });

    const opened: CustomEvent[] = [];
    const handleOpen = (event: Event) => opened.push(event as CustomEvent);
    window.addEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
    fireEvent.click(screen.getByTestId("process-timeline-event-thinking"));

    expect(screen.queryByText("01")).not.toBeInTheDocument();
    expect(screen.queryByText("02")).not.toBeInTheDocument();
    expect(
      screen.getByTestId("process-timeline-event-thinking"),
    ).not.toHaveTextContent(hiddenTail);
    expect(opened.at(-1)?.detail.processEvent.detail).toContain(hiddenTail);
    window.removeEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
  });

  it("keeps saved steps compact and opens their detail in the workbench", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        additional_kwargs: {
          reasoning_content: "First inspect the request.",
        },
      },
      {
        id: "ai-2",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "search-1",
            name: "web_search",
            args: { query: "laser engraving market 2025" },
          },
        ],
      },
    ];

    renderWithProviders(<MessageGroup messages={messages as never} />, {
      locale: "en-US",
    });

    expect(
      screen.queryByTestId("process-details-trigger"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("01")).not.toBeInTheDocument();
    expect(screen.getByText(/laser engraving market 2025/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("process-timeline-event-execution"));

    expect(screen.queryByTitle("Hide saved steps")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Clarify task direction"),
    ).not.toBeInTheDocument();
    expect(
      screen.getAllByText(/laser engraving market 2025/).length,
    ).toBeGreaterThan(0);
  });

  it("keeps completed code-mode traces concrete without action categories", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        additional_kwargs: {
          public_reasoning_summary: "Inspect the user request before editing.",
        },
      },
      {
        id: "ai-2",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "search-1",
            name: "web_search",
            args: { query: "frontend route structure" },
          },
        ],
      },
    ];

    renderWithProviders(
      <MessageGroup codeMode messages={messages as never} />,
      {
        locale: "en-US",
      },
    );

    expect(
      screen.queryByTestId("process-details-trigger"),
    ).not.toBeInTheDocument();
    // Explicit public summaries render as compact chronological trace rows.
    expect(
      screen.getByText("Inspect the user request before editing."),
    ).toBeInTheDocument();
    expect(screen.getByText("frontend route structure")).toBeInTheDocument();
    expect(screen.queryByText(/Search sources/)).not.toBeInTheDocument();
  });

  it("keeps a live code-mode trace compact when the same turn becomes historical", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        additional_kwargs: {
          public_reasoning_summary: "Inspect the user request before editing.",
        },
      },
      {
        id: "ai-2",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "search-1",
            name: "web_search",
            args: { query: "frontend route structure" },
          },
        ],
      },
    ];

    const { rerender } = renderWithProviders(
      <MessageGroup codeMode isLoading messages={messages as never} />,
      {
        locale: "en-US",
      },
    );

    expect(screen.queryByTitle(/Replay/)).not.toBeInTheDocument();
    expect(screen.getByText(/frontend route structure/)).toBeInTheDocument();

    rerender(<MessageGroup codeMode messages={messages as never} />);

    expect(
      screen.queryByTestId("process-details-trigger"),
    ).not.toBeInTheDocument();
    expect(screen.queryByTitle("Hide saved steps")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Clarify task direction"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByTestId("interleaved-process-timeline"),
    ).toBeInTheDocument();
  });

  it("keeps code-mode traces compact while the turn is live", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        additional_kwargs: {
          public_reasoning_summary: "Inspect the user request before editing.",
        },
      },
      {
        id: "ai-2",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "search-1",
            name: "web_search",
            args: { query: "frontend route structure" },
          },
        ],
      },
    ];

    renderWithProviders(
      <MessageGroup codeMode isLoading messages={messages as never} />,
      {
        locale: "en-US",
      },
    );

    // Explicit public summaries remain visible while the turn is live.
    expect(
      screen.getByText("Inspect the user request before editing."),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("interleaved-process-timeline"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Live process")).not.toBeInTheDocument();
    expect(screen.queryByTitle(/Replay/)).not.toBeInTheDocument();
    expect(screen.getByText(/frontend route structure/)).toBeInTheDocument();
  });

  it("shows the current action in chat mode without duplicating the code process strip", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-chat-action",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "search-chat",
            name: "web_search",
            args: { query: "conversational streaming rhythm" },
          },
        ],
      },
    ];

    renderWithProviders(
      <MessageGroup isLoading messages={messages as never} />,
      { locale: "zh-CN" },
    );

    expect(
      screen.getByTestId("interleaved-process-timeline"),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText(/conversational streaming rhythm/).length,
    ).toBeGreaterThan(0);
  });

  it("keeps a confirmation action compact in the live code timeline", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "ask-1",
            name: "ask_user_question",
            args: { question: "是否继续写入文件？" },
          },
        ],
      },
    ];

    renderWithProviders(
      <MessageGroup codeMode isLoading messages={messages as never} />,
      {
        locale: "zh-CN",
      },
    );

    expect(
      screen.getByTestId("interleaved-process-timeline"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("process-timeline-event-execution"),
    ).toBeInTheDocument();
    expect(screen.queryByText("实时进程")).not.toBeInTheDocument();
  });

  it("keeps only the current frame visible when latest trace is kept open", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        additional_kwargs: {
          public_reasoning_summary: "First inspect the request.",
        },
      },
      {
        id: "ai-2",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "search-1",
            name: "web_search",
            args: { query: "laser engraving market 2025" },
          },
        ],
      },
    ];

    renderWithProviders(
      <MessageGroup messages={messages as never} keepOpen />,
      {
        locale: "en-US",
      },
    );

    expect(screen.queryByTitle("View 2 saved steps")).not.toBeInTheDocument();
    expect(screen.queryByTitle(/Replay/)).not.toBeInTheDocument();
    // Explicit public summaries remain in the current frame.
    expect(screen.getByText("First inspect the request.")).toBeInTheDocument();
    expect(screen.getByText(/laser engraving market 2025/)).toBeInTheDocument();

    expect(
      screen.queryByText("Clarify task direction"),
    ).not.toBeInTheDocument();
  });

  it("keeps legacy reasoning private regardless of phase wording", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-lead-in",
        type: "ai",
        content: "",
        additional_kwargs: {
          public_reasoning_summary:
            "这个问题需要先确认赛道边界，否则机会点会太泛。",
        },
      },
      {
        id: "ai-current",
        type: "ai",
        content: "",
        additional_kwargs: {
          public_reasoning_summary: "先拆分候选细分赛道。",
          phase_id: "turn-1:progress:1",
        },
      },
    ];

    renderWithProviders(
      <MessageGroup messages={messages as never} keepOpen />,
      {
        locale: "zh-CN",
      },
    );

    const reasoning = screen.getByTestId("process-timeline-event-thinking");
    expect(reasoning).not.toHaveTextContent(
      "这个问题需要先确认赛道边界，否则机会点会太泛。",
    );
    expect(reasoning).toHaveTextContent("先拆分候选细分赛道。");
    expect(reasoning).toHaveClass("text-muted-foreground/75");
    expect(screen.queryByTitle(/过程回放/)).not.toBeInTheDocument();
  });

  it("does not open a questionnaire for ordinary clarification text inside reasoning steps", () => {
    const message: AIMessage = {
      id: "ai-clarify",
      type: "ai",
      content: "",
      additional_kwargs: {
        reasoning_content: [
          "先问一个关键问题再动手，避免方向偏了：",
          "",
          "你有偏好的行业方向或资源背景吗？ 比如：",
          "",
          "- 你在某个行业有供应链/技术/渠道资源？",
          "- 关注消费品、B2B SaaS、硬件，还是其他？",
          "- 预算规模和团队能力大致是什么量级？",
          "",
          "一句话告诉我方向，我直接开挖。",
        ].join("\n"),
      },
    };

    renderWithProviders(
      <MessageGroup enableClarificationActions messages={[message]} />,
      {
        locale: "zh-CN",
      },
    );

    expect(
      screen.queryByRole("region", { name: "请回答以下问题" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText("更想看哪个行业方向？")).not.toBeInTheDocument();
  });

  it("uses a live status dot without synthetic thinking labels", () => {
    const message: AIMessage = {
      id: "ai-1",
      type: "ai",
      content: "",
      additional_kwargs: {
        reasoning_content: "先确认搜索目标。",
      },
      tool_calls: [
        {
          id: "search-1",
          name: "web_search",
          args: { query: "smart sleep market" },
        },
      ],
    };

    renderWithProviders(<MessageGroup messages={[message]} isLoading />, {
      locale: "zh-CN",
    });

    expect(screen.queryByTitle(/过程回放/)).not.toBeInTheDocument();
    expect(
      screen.getAllByTestId("process-timeline-event-execution").length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText("思考中")).not.toBeInTheDocument();

    expect(screen.getByText(/smart sleep market/)).toBeInTheDocument();
    expect(screen.queryByText("思考中")).not.toBeInTheDocument();
  });

  it("groups consecutive tool calls into a collapsible execution summary", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "search-1",
            name: "web_search",
            args: { query: "laser engraving market 2025" },
          },
        ],
      },
    ];

    renderWithProviders(
      <MessageGroup messages={messages as never} isLoading />,
      {
        locale: "zh-CN",
      },
    );

    expect(
      screen.getByTestId("process-timeline-event-execution"),
    ).toBeInTheDocument();
    expect(screen.getByText(/laser engraving market 2025/)).toBeInTheDocument();
  });

  it("keeps search results out of the compact main timeline", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "search-1",
            name: "web_search",
            args: { query: "laser engraving market 2025" },
          },
        ],
      },
      {
        id: "tool-1",
        type: "tool",
        content: JSON.stringify({
          results: [
            {
              title: "OpenClaw GitHub repo",
              url: "https://github.com/openclaw/openclaw",
            },
            { title: "OpenClaw docs", url: "https://openclaw.dev/docs" },
          ],
        }),
        tool_call_id: "search-1",
      },
    ];

    renderWithProviders(
      <MessageGroup messages={messages as never} isLoading />,
      {
        locale: "zh-CN",
      },
    );

    expect(screen.queryByText("已搜索到 2 个网页")).not.toBeInTheDocument();
    expect(screen.queryByText("OpenClaw GitHub repo")).not.toBeInTheDocument();
    expect(screen.queryByText("OpenClaw docs")).not.toBeInTheDocument();
    expect(
      screen.getByTestId("process-timeline-event-execution"),
    ).toBeInTheDocument();
  });

  it("keeps unknown Action callback text out of the public timeline", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        additional_kwargs: {
          reasoning_content:
            '先整理报告结构。\n\nAction: ipython({"code":"print(\'write file\')"})\n\n继续检查输出文件。',
        },
      },
    ];

    renderWithProviders(
      <MessageGroup messages={messages as never} isLoading />,
      {
        locale: "zh-CN",
      },
    );

    expect(screen.queryByText("已调用")).not.toBeInTheDocument();
    expect(screen.queryByText(/ipython/)).not.toBeInTheDocument();
    // The raw reasoning trace is now surfaced as a thinking row (streaming
    // UX: what the user saw live stays visible on replay), but only the
    // Thought narration survives — the Action callback body is stripped.
    expect(screen.queryByText("继续检查输出文件。")).not.toBeInTheDocument();
    expect(screen.getByText("先整理报告结构。")).toBeInTheDocument();
    expect(screen.queryByText("执行动作")).not.toBeInTheDocument();
    expect(screen.queryByText("整理调研结果")).not.toBeInTheDocument();
    expect(screen.queryByText(/ipython/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Action:/)).not.toBeInTheDocument();
    expect(screen.queryByText("执行中")).not.toBeInTheDocument();
  });

  it("does not infer a second narration lane from ordinary tool-bearing content", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-read",
        type: "ai",
        content: "我先读取消息组件，再把证据串起来。",
        tool_calls: [
          {
            id: "read-message-group",
            name: "read_file",
            args: { path: "frontend/src/messages/message-group.tsx" },
          },
        ],
      },
    ];

    renderWithProviders(<MessageGroup messages={messages} />, {
      locale: "zh-CN",
    });

    expect(
      screen.queryByText("我先读取消息组件，再把证据串起来。"),
    ).not.toBeInTheDocument();
    const executions = screen.getAllByTestId(
      "process-timeline-event-execution",
    );
    const execution = executions[0]!;
    expect(execution).toHaveTextContent("message-group.tsx");
    expect(execution).not.toHaveTextContent("查看文件");
    expect(execution).not.toHaveTextContent("执行动作");
    expect(
      screen.queryByTestId("process-timeline-event-thinking"),
    ).not.toBeInTheDocument();
  });

  it("collapses consecutive tool targets into one quiet evidence row", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "ls-1",
            name: "ls",
            args: { path: "src" },
          },
          {
            id: "read-1",
            name: "read_file",
            args: { path: "src/app.tsx" },
          },
          {
            id: "write-1",
            name: "write_file",
            args: { path: "plan.md" },
          },
        ],
      },
    ];

    renderWithProviders(
      <MessageGroup messages={messages as never} isLoading />,
      {
        locale: "zh-CN",
      },
    );

    expect(screen.queryByText("已浏览目录")).not.toBeInTheDocument();
    expect(screen.queryByText("已读取")).not.toBeInTheDocument();
    const executions = screen.getAllByTestId(
      "process-timeline-event-execution",
    );
    expect(executions).toHaveLength(1);
    expect(executions[0]).toHaveTextContent("app.tsx");
    expect(executions[0]).not.toHaveTextContent("plan.md");
    const execution = executions[0]!;

    const opened: CustomEvent[] = [];
    const handleOpen = (event: Event) => opened.push(event as CustomEvent);
    window.addEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
    fireEvent.click(execution);

    expect(opened.at(-1)?.detail.processEvent).toMatchObject({
      kind: "execution",
      count: 3,
    });
    expect(opened.at(-1)?.detail.processEvent.detail).toContain("src/app.tsx");
    expect(opened.at(-1)?.detail.processEvent.detail).toContain("plan.md");

    expect(screen.queryByText("已浏览目录")).not.toBeInTheDocument();
    expect(screen.queryByText("已读取")).not.toBeInTheDocument();
    expect(opened.at(-1)?.detail.processEvent.detail).toContain("plan.md");
    window.removeEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
  });

  it("keeps crawler transport files out of the conversation lane", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-crawler",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "grep-crawler-cache",
            name: "grep_text",
            args: { path: "working/US10792461B2-full.jsonl" },
          },
        ],
      },
    ];

    renderWithProviders(<MessageGroup messages={messages as never} />, {
      locale: "zh-CN",
    });

    expect(screen.queryByText("US10792461B2-full.jsonl")).toBeNull();
  });

  it("reduces shell workarounds to concrete evidence without leaking local paths", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "root-1",
            name: "list_cwd",
            args: { path: "../.." },
          },
          {
            id: "read-group-1",
            name: "read_file",
            args: {
              path: "/Users/example/Public/echo/echo-agent/frontend/src/components/workspace/messages/message-group.tsx",
            },
          },
          {
            id: "read-1",
            name: "read_file",
            args: {
              path: "/Users/example/Public/echo/echo-agent/runtime/protocol/items.py",
            },
          },
          {
            id: "cat-1",
            name: "exec_shell",
            args: {
              command:
                "cat /Users/example/Public/echo/echo-agent/runtime/protocol/items.py",
            },
          },
          {
            id: "copy-1",
            name: "exec_shell",
            args: {
              command:
                "cp /Users/example/Public/echo/echo-agent/runtime/protocol/items.py /tmp/_items_readonly_copy.py",
            },
          },
          {
            id: "read-reducer-1",
            name: "exec_shell",
            args: {
              command:
                "sed -n '1,240p' /Users/example/Public/echo/echo-agent/frontend/src/core/realtime/reducer.ts",
            },
          },
        ],
      },
    ];

    renderWithProviders(<MessageGroup messages={messages} isLoading />, {
      locale: "zh-CN",
    });

    const executions = screen.getAllByTestId(
      "process-timeline-event-execution",
    );
    const execution = executions[0]!;
    expect(execution).toHaveTextContent(
      "message-group.tsx · items.py · reducer.ts",
    );
    expect(execution).not.toHaveTextContent("/Users/");
    expect(execution).not.toHaveTextContent("../..");
    expect(execution).not.toHaveTextContent("cat ");
    expect(execution).not.toHaveTextContent("cp ");
    expect(execution).not.toHaveTextContent("_items_readonly_copy.py");
    // Shell file-read workarounds and concrete read_file calls are folded into
    // one evidence cluster so the transcript stays concise.
    expect(executions).toHaveLength(1);
  });

  it("attributes every execution inside a commentary interval to its visible anchor", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-progress",
        type: "ai",
        content: "我先核对这几个文件。",
        additional_kwargs: {
          public_progress: true,
          timeline_sequence: 1,
        },
      },
      {
        id: "ai-reads",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "read-a",
            name: "read_file",
            args: { path: "src/a.ts" },
            timelineSequence: 2,
          },
          {
            id: "read-b",
            name: "read_file",
            args: { path: "src/b.ts" },
            timelineSequence: 3,
          },
          {
            id: "read-c",
            name: "read_file",
            args: { path: "src/c.ts" },
            timelineSequence: 4,
          },
        ],
      },
      {
        id: "ai-final",
        type: "ai",
        content: "核对完成。",
      },
    ];

    renderWithProviders(<MessageGroup messages={messages} />, {
      locale: "zh-CN",
    });

    const executions = screen.getAllByTestId(
      "process-timeline-event-execution",
    );
    expect(executions).toHaveLength(1);
    expect(executions[0]).toHaveTextContent("a.ts · b.ts · c.ts");

    const opened: CustomEvent[] = [];
    const handleOpen = (event: Event) => opened.push(event as CustomEvent);
    window.addEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
    fireEvent.click(executions[0]!);

    const detail = opened.at(-1)?.detail.processEvent.detail as string;
    expect(detail).toContain("a.ts");
    expect(detail).toContain("b.ts");
    expect(detail).toContain("c.ts");
    expect(opened.at(-1)?.detail.processEvent.count).toBe(3);
    window.removeEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
  });

  it("folds mixed-kind execution into one conversational receipt", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-progress",
        type: "ai",
        content: "读取并验证。",
        additional_kwargs: {
          public_progress: true,
          timeline_sequence: 1,
        },
      },
      {
        id: "ai-tools",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "read-a",
            name: "read_file",
            args: { path: "src/a.ts" },
            timelineSequence: 2,
          },
          {
            id: "read-b",
            name: "read_file",
            args: { path: "src/b.ts" },
            timelineSequence: 3,
          },
          {
            id: "run-test",
            name: "exec_shell",
            args: { command: "npm test" },
            timelineSequence: 4,
          },
        ],
      },
      {
        id: "ai-final",
        type: "ai",
        content: "完成。",
      },
    ];

    renderWithProviders(<MessageGroup messages={messages} />, {
      locale: "zh-CN",
    });

    const executions = screen.getAllByTestId(
      "process-timeline-event-execution",
    );
    expect(executions).toHaveLength(1);
    expect(executions[0]).toHaveTextContent("a.ts · b.ts");
    expect(executions[0]).toHaveTextContent("执行了 3 个操作");
  });

  it("does not render raw shell commands for a single shell tool call", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-shell",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "shell-1",
            name: "exec_shell",
            args: {
              command: "cat ~/.ssh/id_rsa && npm run typecheck",
            },
          },
        ],
      },
    ];

    renderWithProviders(<MessageGroup messages={messages} isLoading />, {
      locale: "zh-CN",
    });

    expect(screen.getByText("运行")).toBeInTheDocument();
    expect(screen.queryByText(/cat ~\/.ssh\/id_rsa/)).not.toBeInTheDocument();
    expect(screen.queryByText(/npm run typecheck/)).not.toBeInTheDocument();
  });

  it("uses a public fallback for unknown tool names", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-unknown-tool",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "custom-1",
            name: "mcp_secret_probe",
            args: {
              token: "sk-test-should-not-render",
            },
          },
        ],
      },
    ];

    renderWithProviders(<MessageGroup messages={messages} isLoading />, {
      locale: "zh-CN",
    });

    expect(screen.getByText("执行操作")).toBeInTheDocument();
    expect(screen.queryByText(/mcp_secret_probe/)).not.toBeInTheDocument();
    expect(screen.queryByText(/sk-test/)).not.toBeInTheDocument();
  });

  it("renders capability tools as a localized human action", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-capability-tool",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "capability-1",
            name: "use_capability",
            args: { capability: "deep_research" },
          },
        ],
      },
    ];

    renderWithProviders(<MessageGroup messages={messages} isLoading />, {
      locale: "zh-CN",
    });

    expect(screen.getByText("使用能力")).toBeInTheDocument();
    expect(screen.getByText("deep_research")).toBeInTheDocument();
    expect(screen.queryByText("use_capability")).not.toBeInTheDocument();
  });

  it("keeps explicit human descriptions for unknown tools", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-described-tool",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "custom-2",
            name: "mcp_custom_bridge",
            args: {
              description: "同步外部任务状态",
            },
          },
        ],
      },
    ];

    renderWithProviders(<MessageGroup messages={messages} isLoading />, {
      locale: "zh-CN",
    });

    expect(screen.getByText("同步外部任务状态")).toBeInTheDocument();
    expect(screen.queryByText(/mcp_custom_bridge/)).not.toBeInTheDocument();
  });
});

describe("MessageGroup streaming lifecycle", () => {
  it("interleaves thinking, answer, and execution as quiet timeline rows", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-thinking",
        type: "ai",
        content: "",
        additional_kwargs: {
          public_reasoning_summary: "先确认需求和现有上下文",
        },
      },
      {
        id: "ai-answer-and-tool",
        type: "ai",
        content: "我先给你一个方向，同时继续检查实现。",
        tool_calls: [
          {
            id: "read-1",
            name: "read_file",
            args: { path: "src/chat.tsx" },
          },
        ],
      },
    ];
    const opened: CustomEvent[] = [];
    const handleOpen = (event: Event) => opened.push(event as CustomEvent);
    window.addEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);

    renderWithProviders(
      <MessageGroup messages={messages as never} isLoading />,
      { locale: "zh-CN" },
    );

    const thinking = screen.getByTestId("process-timeline-event-thinking");
    const answer = screen.getByText("我先给你一个方向，同时继续检查实现。");
    const execution = screen.getByTestId("process-timeline-event-execution");

    expect(
      thinking.compareDocumentPosition(answer) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      answer.compareDocumentPosition(execution) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(thinking.className).not.toMatch(/\b(?:border|rounded|bg-)/);
    expect(execution.className).not.toMatch(/\b(?:border|rounded|bg-)/);
    expect(thinking).toHaveAttribute("data-process-event-id", "ai-thinking");
    expect(execution).toHaveAttribute("data-process-event-id", "read-1");

    fireEvent.click(thinking);
    expect(opened.at(-1)?.detail).toMatchObject({
      tab: "agent",
      eventId: "ai-thinking",
      eventKind: "thinking",
      view: "summary",
      processEvent: {
        kind: "thinking",
        summary: "先确认需求和现有上下文",
        detail: "先确认需求和现有上下文",
        status: "done",
        count: 1,
      },
    });
    fireEvent.click(execution);
    expect(opened.at(-1)?.detail).toMatchObject({
      tab: "agent",
      eventId: "read-1",
      eventKind: "execution",
      view: "trace",
      processEvent: {
        kind: "execution",
        status: "running",
        count: 1,
      },
    });

    window.removeEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
  });

  it("keeps later thinking below the answer it followed", () => {
    // 正文按它产生的时刻落位：早于正文的过程在上，晚于正文的思考在下。
    const messages: AIMessage[] = [
      {
        id: "ai-think-1",
        type: "ai",
        content: "",
        additional_kwargs: { public_reasoning_summary: "先看现有实现" },
      },
      {
        id: "ai-answer",
        type: "ai",
        content: "方向是这样，我继续核对。",
        tool_calls: [
          { id: "read-1", name: "read_file", args: { path: "a.ts" } },
        ],
      },
      {
        id: "ai-think-2",
        type: "ai",
        content: "",
        additional_kwargs: { public_reasoning_summary: "再核对一处调用点" },
      },
    ];

    renderWithProviders(
      <MessageGroup messages={messages as never} isLoading />,
      { locale: "zh-CN" },
    );

    const answer = screen.getByText("方向是这样，我继续核对。");
    const first = screen.getByText(/先看现有实现/);
    const later = screen.getByText(/再核对一处调用点/);

    // 早于正文的思考在正文之前
    expect(
      first.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // 晚于正文的思考在正文之后，而不是被顶回上方
    expect(
      answer.compareDocumentPosition(later) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("opens right-side process details without leaked protocol or internal blocks", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-thinking",
        type: "ai",
        content: "",
        additional_kwargs: {
          public_reasoning_summary:
            "<read_only> </read_only>\n<TextBlock>先确认公开进展</TextBlock>\nAction: read_file\n失败原因：token=super-secret\n<ToolCallBlock>private tool args</ToolCallBlock>",
        },
      },
      {
        id: "ai-tool",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "read-1",
            name: "read_file",
            args: { path: "src/chat.tsx" },
          },
        ],
      },
    ];
    const opened: CustomEvent[] = [];
    const handleOpen = (event: Event) => opened.push(event as CustomEvent);
    window.addEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);

    renderWithProviders(
      <MessageGroup messages={messages as never} isLoading />,
      { locale: "zh-CN" },
    );

    expect(
      screen.queryByText(/read_only|ToolCallBlock|read_file/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/super-secret/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("process-timeline-event-thinking"));

    const processEvent = opened.at(-1)?.detail.processEvent;
    expect(processEvent).toMatchObject({
      kind: "thinking",
      summary: expect.stringContaining("先确认公开进展"),
    });
    expect(processEvent?.detail).toContain("先确认公开进展");
    expect(JSON.stringify(processEvent)).not.toMatch(
      /read_only|TextBlock|ToolCallBlock|private tool args|read_file|super-secret/i,
    );

    window.removeEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
  });

  it("cleans public progress body before rendering it in the main timeline", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-public-progress",
        type: "ai",
        content:
          "<read_only> </read_only>\n<TextBlock>已确认主线展示。</TextBlock>\nAction: read_file\nObservation: token=super-secret",
        additional_kwargs: {
          public_progress: true,
        },
      },
    ];
    const opened: CustomEvent[] = [];
    const handleOpen = (event: Event) => opened.push(event as CustomEvent);
    window.addEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);

    renderWithProviders(
      <MessageGroup messages={messages as never} isLoading />,
      { locale: "zh-CN" },
    );

    expect(screen.getByText(/已确认主线展示/)).toBeInTheDocument();
    expect(
      screen.queryByText(/read_only|TextBlock|read_file|Observation/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/super-secret/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("public-progress-event"));
    const processEvent = opened.at(-1)?.detail.processEvent;
    expect(processEvent?.summary).toContain("已确认主线展示");
    expect(JSON.stringify(processEvent)).not.toMatch(
      /read_only|TextBlock|read_file|Observation|super-secret/i,
    );

    window.removeEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
  });

  it("keeps a thinking checkpoint visible during a long tool run", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-thinking-anchor",
        type: "ai",
        content: "",
        additional_kwargs: { public_reasoning_summary: "先梳理架构边界" },
      },
      {
        id: "ai-long-tool-run",
        type: "ai",
        content: "",
        tool_calls: Array.from({ length: 8 }, (_, index) => ({
          id: `read-${index}`,
          name: "read_file",
          args: { path: `src/file-${index}.ts` },
        })),
      },
    ];

    renderWithProviders(
      <MessageGroup messages={messages as never} isLoading />,
      { locale: "zh-CN" },
    );

    expect(
      screen.getByTestId("process-timeline-event-thinking"),
    ).toHaveAttribute("data-process-event-id", "ai-thinking-anchor");
    const visibleExecutions = screen.getAllByTestId(
      "process-timeline-event-execution",
    );
    expect(visibleExecutions).toHaveLength(1);
    expect(visibleExecutions[0]).toHaveAttribute(
      "data-process-event-id",
      "read-7",
    );
    expect(visibleExecutions[0]).toHaveTextContent(
      "file-5.ts · file-6.ts · file-7.ts +5",
    );

    const opened: CustomEvent[] = [];
    const handleOpen = (event: Event) => opened.push(event as CustomEvent);
    window.addEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
    fireEvent.click(visibleExecutions[0]!);
    expect(opened.at(-1)?.detail.processEvent).toMatchObject({ count: 8 });
    expect(opened.at(-1)?.detail.processEvent.detail).toContain(
      "src/file-0.ts",
    );
    expect(opened.at(-1)?.detail.processEvent.detail).toContain(
      "src/file-7.ts",
    );
    window.removeEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
  });

  it("transitions from streaming to completed without losing tool calls", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "search-1",
            name: "web_search",
            args: { query: "test query" },
          },
          {
            id: "read-1",
            name: "read_file",
            args: { path: "notes.md" },
          },
        ],
      },
      {
        id: "tool-1",
        type: "tool",
        content: "search results here",
        tool_call_id: "search-1",
      },
      {
        id: "tool-2",
        type: "tool",
        content: "file content here",
        tool_call_id: "read-1",
      },
    ];

    const { rerender } = renderWithProviders(
      <MessageGroup messages={messages as never} isLoading />,
      { locale: "en-US" },
    );

    expect(screen.getAllByText(/notes\.md/).length).toBeGreaterThan(0);

    rerender(<MessageGroup messages={messages as never} />);

    expect(
      screen.queryByTestId("process-details-trigger"),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("process-timeline-event-execution"));
    expect(screen.getAllByText(/test query/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/notes\.md/).length).toBeGreaterThan(0);
  });

  it("keeps public reasoning summaries stable when streaming tokens arrive", () => {
    const makeMessages = (reasoning: string): AIMessage[] => [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        additional_kwargs: { public_reasoning_summary: reasoning },
      },
    ];

    const { rerender } = renderWithProviders(
      <MessageGroup
        messages={makeMessages("Thinking about phase one")}
        isLoading
      />,
      { locale: "en-US" },
    );

    expect(screen.getByText(/phase one/)).toBeInTheDocument();

    rerender(
      <MessageGroup
        messages={makeMessages("Thinking about phase one and phase two")}
        isLoading
      />,
    );

    expect(screen.getByText(/phase one and phase two/)).toBeInTheDocument();
  });

  it("handles empty message array gracefully", () => {
    const { container } = renderWithProviders(<MessageGroup messages={[]} />, {
      locale: "en-US",
    });
    expect(container).toBeEmptyDOMElement();
  });

  it("shows error state correctly when tool fails during streaming", () => {
    const messages: Message[] = [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "tool-1",
            name: "web_search",
            args: { query: "test" },
          },
        ],
      },
      {
        id: "tool-1",
        type: "tool",
        content: "",
        tool_call_id: "tool-1",
        additional_kwargs: { status: "error", error: "Search failed" },
      },
    ];

    renderWithProviders(<MessageGroup messages={messages} isLoading />, {
      locale: "en-US",
    });

    expect(screen.getByText(/test/)).toBeInTheDocument();
  });

  it("code mode live process strip transitions from running to completed", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "read-1",
            name: "read_file",
            args: { path: "test.ts" },
          },
        ],
      },
      {
        id: "tool-read-1",
        type: "tool",
        content: "file content",
        tool_call_id: "read-1",
      },
    ];

    const { rerender } = renderWithProviders(
      <MessageGroup codeMode isLoading messages={messages as never} />,
      { locale: "en-US" },
    );

    expect(
      screen.getByTestId("interleaved-process-timeline"),
    ).toBeInTheDocument();

    rerender(<MessageGroup codeMode messages={messages as never} />);

    expect(
      screen.getByTestId("interleaved-process-timeline"),
    ).toBeInTheDocument();
  });

  it("keeps private reasoning details stable across streaming updates", () => {
    const makeMessages = (extraText: string): AIMessage[] => [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        additional_kwargs: {
          public_reasoning_summary: "First thinking step that is visible.",
        },
      },
      {
        id: "ai-2",
        type: "ai",
        content: "",
        additional_kwargs: {
          public_reasoning_summary: `Second thinking step. ${extraText}`,
        },
      },
    ];

    const { rerender } = renderWithProviders(
      <MessageGroup messages={makeMessages("Initial")} keepOpen />,
      { locale: "en-US" },
    );

    expect(screen.getAllByText(/Second thinking step/).length).toBeGreaterThan(
      0,
    );
    const opened: CustomEvent[] = [];
    const handleOpen = (event: Event) => opened.push(event as CustomEvent);
    window.addEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
    const thinkingRow = screen.getByTestId("process-timeline-event-thinking");
    fireEvent.click(thinkingRow);
    expect(
      screen.getByTestId("process-timeline-event-thinking"),
    ).not.toHaveTextContent("First thinking step");
    expect(opened.at(-1)?.detail.processEvent.detail).toContain(
      "First thinking step",
    );

    rerender(
      <MessageGroup
        messages={makeMessages("Updated with more content")}
        keepOpen
      />,
    );

    expect(
      screen.queryByTestId("process-details-trigger"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByTestId("process-timeline-event-thinking"),
    ).not.toHaveTextContent("First thinking step");
    expect(screen.getAllByText(/Second thinking step/).length).toBeGreaterThan(
      0,
    );
    window.removeEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpen);
  });

  it("groups mixed content + tool calls into processing + assistant groups", () => {
    const longAnswer = [
      "# Summary of findings",
      "",
      "Here is a detailed analysis that exceeds the final-answer threshold.",
      "",
      "1. First point about the research",
      "2. Second point about the data",
      "3. Third point about the conclusion",
      "4. Fourth point with additional context",
      "",
      "This paragraph adds enough length to cross the 320-character threshold ",
      "so the message is treated as a final answer rather than a preamble. ",
      "The distinction matters because final answers render as standalone ",
      "assistant content, while short preambles fold into the process timeline.",
    ].join("\n");

    const message: AIMessage = {
      id: "ai-mixed",
      type: "ai",
      content: longAnswer,
      tool_calls: [
        {
          id: "search-1",
          name: "web_search",
          args: { query: "reference docs" },
        },
      ],
    };

    const groups = groupMessages([message], (g) => g);
    expect(groups.length).toBe(2);
    expect(groups[0]?.type).toBe("assistant:processing");
    expect(groups[1]?.type).toBe("assistant");
    expect(groups[0]?.messages).toContain(message);
    expect(groups[1]?.messages).toContain(message);

    renderWithProviders(
      <MessageGroup messages={groups[0]!.messages} isLoading />,
      {
        locale: "en-US",
      },
    );

    expect(screen.getByText(/reference docs/)).toBeInTheDocument();
  });
});

describe("MessageGroup 紧凑模式叙事保真", () => {
  // 构造 6 轮「意图 → 工具 → 事实」长任务：意图消息的 reasoning 紧跟上一轮
  // 工具调用（iteration 由此递增），事实以 public_progress checkpoint 呈现。
  function buildLongRunMessages(): AIMessage[] {
    const intentMessage = (round: number): AIMessage => ({
      id: `ai-intent-${round}`,
      type: "ai",
      content: "",
      additional_kwargs: {
        public_reasoning_summary: `第 ${round} 轮意图：读取 round-${round}.ts`,
      },
    });
    const toolsMessage = (round: number): AIMessage => ({
      id: `ai-tools-${round}`,
      type: "ai",
      content: "",
      tool_calls: [
        {
          id: `call-${round}`,
          name: "read_file",
          args: { path: `src/round-${round}.ts` },
        },
      ],
    });
    const factMessage = (round: number): AIMessage => ({
      id: `ai-fact-${round}`,
      type: "ai",
      content: `已确认第 ${round} 轮事实`,
      additional_kwargs: { public_progress: true },
    });

    const messages: AIMessage[] = [intentMessage(1), toolsMessage(1)];
    for (let round = 2; round <= 6; round += 1) {
      messages.push(
        intentMessage(round),
        factMessage(round - 1),
        toolsMessage(round),
      );
    }
    messages.push(factMessage(6));
    return messages;
  }

  it("长任务压缩后每轮至少保留一个叙事锚点、最新事实必留", () => {
    renderWithProviders(<MessageGroup messages={buildLongRunMessages()} />, {
      locale: "zh-CN",
    });

    // Long settled runs stay out of the DOM until the reader asks for replay.
    expect(screen.queryByTestId("public-progress-event")).toBeNull();
    expect(screen.getByTestId("process-replay-toggle")).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    fireEvent.click(screen.getByTestId("process-replay-toggle"));

    // 6 个事实 checkpoint 全部被语义保底（每轮兜底锚点 + 最新事实）
    expect(screen.getAllByTestId("public-progress-event")).toHaveLength(6);
    for (let round = 1; round <= 6; round += 1) {
      expect(
        screen.getByLabelText(`已确认第 ${round} 轮事实`),
      ).toBeInTheDocument();
    }
    // 思考是时间线事件而非状态挂件：每轮意图都留在它发生的位置，
    // 这样成绩单读起来是 思考 → 执行 → 事实 → 思考，而不是一个
    // 永远停在原地的“最新思考”窗口。
    for (let round = 1; round <= 6; round += 1) {
      expect(
        screen.getByText(new RegExp(`第 ${round} 轮意图`)),
      ).toBeInTheDocument();
    }
    expect(
      screen.getAllByTestId("process-timeline-event-thinking"),
    ).toHaveLength(6);
    // 纯过程组没有最终回答，不出现分界
    expect(screen.queryByTestId("final-answer-boundary")).toBeNull();
  });
});

describe("selectCompactTimelineItems 语义保真采样", () => {
  // 手搓 TimelineItem：每轮 intent commentary + 工具 + fact commentary
  function buildRoundItems(withRoles: boolean) {
    const items: TimelineItem[] = [];
    const intents: TimelineItem[] = [];
    const facts: TimelineItem[] = [];
    for (let round = 1; round <= 6; round += 1) {
      const intent: TimelineItem = {
        id: `intent-${round}`,
        type: "commentary",
        step: {
          id: `intent-step-${round}`,
          type: "commentary",
          commentary: `第 ${round} 轮意图`,
          iteration: round,
        },
        ...(withRoles ? { role: "intent" as const } : {}),
      };
      const tool: TimelineItem = {
        id: `tool-${round}`,
        type: "toolCall",
        step: {
          id: `call-${round}`,
          type: "toolCall",
          name: "read_file",
          args: { path: `src/round-${round}.ts` },
          iteration: round,
        },
        ...(withRoles ? { role: "execution" as const } : {}),
      };
      const fact: TimelineItem = {
        id: `fact-${round}`,
        type: "commentary",
        step: {
          id: `fact-step-${round}`,
          type: "commentary",
          commentary: `第 ${round} 轮事实`,
          iteration: round,
        },
        ...(withRoles ? { role: "fact" as const } : {}),
      };
      items.push(intent, tool, fact);
      intents.push(intent);
      facts.push(fact);
    }
    return { items, intents, facts };
  }

  it("每个 iteration 必留 intent 条目、最新 fact 必留，且返回原引用", () => {
    const { items, intents, facts } = buildRoundItems(true);

    const result = selectCompactTimelineItems(items);

    for (const intent of intents) {
      expect(result).toContain(intent);
    }
    expect(result).toContain(facts[5]!);
    // 保底已超额，较早的 fact 不再额外补样
    expect(result).not.toContain(facts[1]!);
    expect(result).not.toContain(facts[2]!);
    // 引用相等：选择器返回原 item，不破坏下游 React memo
    for (const item of result) {
      expect(items).toContain(item);
    }
    expect(result[0]).toBe(items[0]);
  });

  it("role 缺失的旧数据按位置兜底，行为正常", () => {
    const { items, intents, facts } = buildRoundItems(false);

    const result = selectCompactTimelineItems(items);

    // 角色由 assignTimelineRoles 在判定副本上补齐：第 1 轮首个 commentary
    // 推断为 intent，其余轮按位置取首个 commentary 兜底，最新 fact 必留
    for (const intent of intents) {
      expect(result).toContain(intent);
    }
    expect(result).toContain(facts[5]!);
    for (const item of result) {
      expect(items).toContain(item);
    }
  });

  it("短对话（commentary ≤ 4）行为完全不变", () => {
    const items: TimelineItem[] = [];
    const commentaries: TimelineItem[] = [];
    for (let round = 1; round <= 4; round += 1) {
      const commentary: TimelineItem = {
        id: `commentary-${round}`,
        type: "commentary",
        step: {
          id: `commentary-step-${round}`,
          type: "commentary",
          commentary: `进展 ${round}`,
          iteration: round,
        },
        role: round === 1 ? "intent" : "fact",
      };
      commentaries.push(commentary);
      items.push(commentary);
      if (round < 4) {
        items.push({
          id: `tool-${round}`,
          type: "toolCall",
          step: {
            id: `call-${round}`,
            type: "toolCall",
            name: "read_file",
            args: { path: `src/round-${round}.ts` },
            iteration: round,
          },
          role: "execution",
        });
      }
    }

    const result = selectCompactTimelineItems(items);

    for (const commentary of commentaries) {
      expect(result).toContain(commentary);
    }
  });
});

describe("MessageGroup 最终回答视觉分层", () => {
  const longAnswer = [
    "# 调查结论",
    "",
    "这是一段足够长的最终回答，用于触发最终回答判定阈值。",
    "",
    "1. 第一点结论",
    "2. 第二点结论",
    "3. 第三点结论",
    "4. 第四点结论",
    "",
    "这一段继续补充正文长度，确保超过 320 字符的最终回答阈值，",
    "使该消息被视为最终回答而不是过程旁白，从而在下方独立渲染。",
  ].join("\n");

  const answerMessage: AIMessage = {
    id: "ai-answer-with-tools",
    type: "ai",
    content: longAnswer,
    tool_calls: [
      {
        id: "search-1",
        name: "web_search",
        args: { query: "reference docs" },
      },
    ],
  };

  it("流式结束后过程段落与最终回答之间出现分界", () => {
    renderWithProviders(<MessageGroup messages={[answerMessage]} />, {
      locale: "zh-CN",
    });

    expect(screen.getByTestId("final-answer-boundary")).toBeInTheDocument();
  });

  it("流式进行中不渲染分界，避免跳动", () => {
    renderWithProviders(<MessageGroup messages={[answerMessage]} isLoading />, {
      locale: "zh-CN",
    });

    expect(screen.queryByTestId("final-answer-boundary")).toBeNull();
  });
});

describe("MessageGroup 收敛摘要行", () => {
  // 构造两个 phase 的流式任务：phase-1 已完成（含 commentary + 3 个 read_file），
  // phase-2 进行中。公开 commentary 必须保持完整，只有工具活动收敛。
  function buildMultiPhaseMessages(): AIMessage[] {
    return [
      // phase-1: commentary (phase intent) + 3 个 read_file
      {
        id: "ai-phase1-intent",
        type: "ai",
        content: "了解代码结构",
        additional_kwargs: {
          public_progress: true,
          phase_id: "turn-1:progress:1",
        },
      },
      {
        id: "ai-phase1-tools",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "read-1",
            name: "read_file",
            args: { path: "src/auth.ts" },
            phaseId: "turn-1:progress:1",
          },
          {
            id: "read-2",
            name: "read_file",
            args: { path: "src/middleware.ts" },
            phaseId: "turn-1:progress:1",
          },
          {
            id: "read-3",
            name: "read_file",
            args: { path: "src/config.ts" },
            phaseId: "turn-1:progress:1",
          },
        ],
      },
      // phase-2: 进行中
      {
        id: "ai-phase2-intent",
        type: "ai",
        content: "",
        additional_kwargs: {
          public_reasoning_summary: "开始修改代码",
          phase_id: "turn-1:progress:2",
        },
      },
    ];
  }

  it("公开进度保持完整，不生成重复的阶段折叠行", () => {
    renderWithProviders(
      <MessageGroup messages={buildMultiPhaseMessages() as never} isLoading />,
      { locale: "zh-CN" },
    );

    const commentary = screen.getByTestId("public-progress-event");
    expect(commentary).toHaveTextContent("了解代码结构");
    expect(commentary).toHaveClass("narrative-progress-row", "text-foreground");
    expect(screen.queryByTestId("collapsed-history-phase")).toBeNull();
    // 工具自身仍可聚合，不借用公开进度文案充当折叠标题。
    expect(
      screen.getByTestId("process-timeline-event-execution"),
    ).toHaveTextContent("查看了 3 个文件");
  });

  it("展开工具详情不会折叠公开进度", () => {
    renderWithProviders(
      <MessageGroup messages={buildMultiPhaseMessages() as never} isLoading />,
      { locale: "zh-CN" },
    );

    fireEvent.click(screen.getByTestId("aggregated-group-toggle"));
    expect(screen.getByTestId("aggregated-group-children")).toHaveAttribute(
      "data-state",
      "open",
    );
    expect(screen.getByTestId("public-progress-event")).toHaveTextContent(
      "了解代码结构",
    );
  });
});

describe("reasoning duration replay", () => {
  it("深度思考按钮只有一次标题，图标不重复朗读", () => {
    const message = {
      id: "ai-deep-thinking-label",
      type: "ai",
      content: "",
      additional_kwargs: {
        reasoning_content: "深入分析需求与执行证据。".repeat(60),
        reasoning_duration_ms: 12_000,
      },
    } as AIMessage;

    renderWithProviders(<MessageGroup messages={[message]} />, {
      locale: "zh-CN",
    });

    const thinkingRow = screen.getByTestId("process-timeline-event-thinking");
    expect(thinkingRow).toHaveAccessibleName(/^深度思考思考了 12\.0s/);
    expect(thinkingRow).not.toHaveAccessibleName(/深度思考.*深度思考/);
  });

  it("回放时显示后端持久化的思考耗时", () => {
    const message = {
      id: "ai-1",
      type: "ai",
      content: "",
      additional_kwargs: {
        reasoning_content: "分析需求",
        reasoning_duration_ms: 3500,
      },
    } as AIMessage;

    renderWithProviders(<MessageGroup messages={[message]} />, {
      locale: "zh-CN",
    });

    const thinkingRow = screen.getByTestId("process-timeline-event-thinking");
    expect(thinkingRow).toHaveTextContent("思考了 3.5s");
  });

  it("不把 provider 的英文计划标签当作公开思考展示", () => {
    const message = {
      id: "ai-provider-heading",
      type: "ai",
      content: "已完成核对。",
      additional_kwargs: {
        reasoning_content:
          "**Planning patent analysis report****Creating detailed task list**",
        public_progress: true,
      },
    } as AIMessage;

    renderWithProviders(<MessageGroup messages={[message]} />, {
      locale: "zh-CN",
    });

    expect(screen.getByText("已完成核对。")).toBeInTheDocument();
    expect(screen.queryByText(/Planning patent analysis report/)).toBeNull();
  });

  it("reasoning_duration_ms 为 0 时不显示耗时", () => {
    const message = {
      id: "ai-1",
      type: "ai",
      content: "",
      additional_kwargs: {
        public_reasoning_summary: "分析需求",
        reasoning_duration_ms: 0,
      },
    } as AIMessage;

    renderWithProviders(<MessageGroup messages={[message]} />, {
      locale: "zh-CN",
    });

    const thinkingRow = screen.getByTestId("process-timeline-event-thinking");
    expect(thinkingRow).not.toHaveTextContent(/思考了/);
  });

  it("缺少 reasoning_duration_ms 时不显示耗时", () => {
    const message = {
      id: "ai-1",
      type: "ai",
      content: "",
      additional_kwargs: {
        public_reasoning_summary: "分析需求",
      },
    } as AIMessage;

    renderWithProviders(<MessageGroup messages={[message]} />, {
      locale: "zh-CN",
    });

    const thinkingRow = screen.getByTestId("process-timeline-event-thinking");
    expect(thinkingRow).not.toHaveTextContent(/思考了/);
  });
});

describe("reasoning live timer from backend timestamp", () => {
  it("starts the live timer from reasoning_started_at", () => {
    vi.useFakeTimers();
    try {
      // 用 public_reasoning_summary 让最后一条 compact timeline item 是
      // reasoningGroup，从而 isCurrentlyThinking=true。reasoning_started_at
      // 指向 3.5 秒前；推进 1.5 秒后首个 interval tick 落在 +1s 处，
      // elapsed = (T0+1s) - (T0-3.5s) = 4.5s，越过 200ms 阈值，应渲染
      // `t.messageGrouping.thinkingDuration` 即「思考了 ...」。
      const message = {
        id: "ai-1",
        type: "ai",
        content: "",
        additional_kwargs: {
          reasoning_content: "分析需求证据",
          reasoning_started_at: new Date(Date.now() - 3500).toISOString(),
        },
      } as AIMessage;

      renderWithProviders(<MessageGroup messages={[message]} isLoading />, {
        locale: "zh-CN",
      });

      act(() => {
        vi.advanceTimersByTime(1500);
      });

      const thinking = screen.getByTestId("process-timeline-event-thinking");
      expect(thinking).toBeInTheDocument();
      expect(thinking).toHaveTextContent("思考了");
    } finally {
      vi.useRealTimers();
    }
  });

  it("falls back to Date.now() when reasoning_started_at is missing", () => {
    vi.useFakeTimers();
    try {
      // 旧数据没有 reasoning_started_at，计时器回退到 Date.now()；
      // 推进时间后不崩溃，thinking 行仍可定位。
      const message = {
        id: "ai-1",
        type: "ai",
        content: "",
        additional_kwargs: {
          public_reasoning_summary: "正在分析",
        },
      } as AIMessage;

      renderWithProviders(<MessageGroup messages={[message]} isLoading />, {
        locale: "zh-CN",
      });

      act(() => {
        vi.advanceTimersByTime(1500);
      });

      expect(
        screen.getByTestId("process-timeline-event-thinking"),
      ).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows a live execution output window while running, folds after settle", () => {
    const messages: AIMessage[] = [
      {
        id: "ai-exec",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "tc-exec",
            name: "npm test",
            args: {
              command: "npm test",
              output: "PASS test1\nPASS test2\nrunning...",
            },
            type: "tool_call",
          },
        ],
      },
    ];

    const { rerender } = renderWithProviders(
      <MessageGroup messages={messages as never} isLoading />,
      { locale: "en-US" },
    );

    // While running: live stdout typewriter window is visible.
    const stream = screen.getByTestId("live-exec-stream");
    expect(stream).toHaveTextContent("PASS test1");

    // Once settled: window folds away, summary row remains.
    rerender(<MessageGroup messages={messages as never} />);
    expect(screen.queryByTestId("live-exec-stream")).not.toBeInTheDocument();
  });
});

describe("conversation detail level (对话细节级别)", () => {
  const SETTINGS_KEY = "echo.local-settings";

  function seedDetailLevel(level: "low" | "medium" | "high") {
    localStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({ display: { conversation_detail_level: level } }),
    );
  }

  function detailMessages(): Message[] {
    return [
      {
        id: "ai-think",
        type: "ai",
        content: "",
        additional_kwargs: {
          reasoning_content:
            "权衡:用户可能期望方案 A 更快落地,但方案 B 在长期维护上更稳妥。" +
            "如果选择 A,后续扩展需要重构,模块边界需要重新设计,测试也要补齐。" +
            "如果选择 B,首轮开发成本更高,但后续每次迭代都会更省力,风险更低。" +
            "综合团队当前节奏、交付压力与长期可维护性,还需要再权衡一次," +
            "不能因为交付压力就草率决定,至少要把两边的隐性成本都列清楚。",
        },
      } as AIMessage,
      {
        id: "ai-tool",
        type: "ai",
        content: "",
        tool_calls: [
          { id: "tc-1", name: "read_file", args: { path: "src/a.ts" } },
        ],
      } as AIMessage,
      {
        id: "ai-final",
        type: "ai",
        content: "最终结果:已读取 src/a.ts 并确认结构。",
      } as AIMessage,
    ];
  }

  it("low hides thinking/tool rows (process lane empty)", () => {
    seedDetailLevel("low");
    renderWithProviders(<MessageGroup messages={detailMessages()} />, {
      locale: "zh-CN",
    });

    expect(
      screen.queryByTestId("process-timeline-event-thinking"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("process-timeline-event-execution"),
    ).not.toBeInTheDocument();
    // No thinking/execution/commentary rows survive in the process lane.
    const lanes = screen.queryAllByTestId("interleaved-process-timeline");
    for (const lane of lanes) {
      expect(lane.textContent ?? "").toBe("");
    }
  });

  it("high shows thinking/tool rows but keeps private thinking collapsed", () => {
    seedDetailLevel("high");
    renderWithProviders(<MessageGroup messages={detailMessages()} />, {
      locale: "zh-CN",
    });

    // Thinking + execution rows are visible.
    expect(
      screen.getByTestId("process-timeline-event-thinking"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("process-timeline-event-execution"),
    ).toBeInTheDocument();
    // Even high detail keeps private reasoning closed until the user asks;
    // public progress is already rendered separately as normal prose.
    expect(screen.getByTestId("thinking-row-content")).toHaveAttribute(
      "data-state",
      "closed",
    );
    fireEvent.click(screen.getByTestId("thinking-row-toggle"));
    expect(screen.getByTestId("thinking-row-content")).toHaveAttribute(
      "data-state",
      "open",
    );
    expect(
      screen.getByTestId("thinking-row-content").firstElementChild,
    ).toHaveClass("ml-4");
  });

  it("high still folds a large aggregate until the user expands it", () => {
    seedDetailLevel("high");
    const messages: Message[] = [
      {
        id: "ai-large-tool-run",
        type: "ai",
        content: "",
        tool_calls: Array.from({ length: 9 }, (_, index) => ({
          id: `read-${index + 1}`,
          name: "read_file",
          args: { path: `src/file-${index + 1}.ts` },
        })),
      } as AIMessage,
    ];

    renderWithProviders(<MessageGroup messages={messages} />, {
      locale: "zh-CN",
    });

    expect(screen.getByTestId("aggregated-group-children")).toHaveAttribute(
      "data-state",
      "closed",
    );
    fireEvent.click(screen.getByTestId("aggregated-group-toggle"));
    expect(screen.getByTestId("aggregated-group-children")).toHaveAttribute(
      "data-state",
      "open",
    );
  });

  it("medium keeps rows visible but collapses the thinking detail (default)", () => {
    seedDetailLevel("medium");
    renderWithProviders(<MessageGroup messages={detailMessages()} />, {
      locale: "zh-CN",
    });

    expect(
      screen.getByTestId("process-timeline-event-thinking"),
    ).toBeInTheDocument();
    // Medium collapses the detail by default (Radix keeps the node in the
    // DOM but marks it closed + hidden).
    expect(screen.getByTestId("thinking-row-content")).toHaveAttribute(
      "data-state",
      "closed",
    );
  });
});
