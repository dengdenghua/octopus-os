import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type * as React from "react";
import { describe, expect, test, vi } from "vitest";

import type { Message } from "@/core/api/types";
import type { BaseStream } from "@/core/api/use-stream-types";
import type { AgentThreadState } from "@/core/threads";
import { SubtasksProvider } from "@/core/tasks/context";
import { groupMessages } from "@/core/messages/utils";
import { renderWithProviders } from "@/test/harness";

import { ThreadProviders } from "./context";
import type { LiveToolEvent } from "../live-tool-timeline";
import {
  buildTurnMarkers,
  HistoricalTurnBoundary,
  MessageList,
  nearestTurnKeyByViewportCenter,
  partitionMessageGroupsIntoTurns,
  turnMarkerKindFromMessages,
  visibleTurnMarkerWindow,
} from "./message-list";

vi.mock("@/components/ai-elements/conversation", () => ({
  Conversation: ({
    children,
    className,
  }: {
    children: React.ReactNode;
    className?: string;
  }) => <div className={className}>{children}</div>,
  ConversationContent: ({
    children,
    className,
  }: {
    children: React.ReactNode;
    className?: string;
  }) => <div className={className}>{children}</div>,
  ConversationScrollButton: ({
    activityKey: _activityKey,
    activityLabel: _activityLabel,
    children,
    style,
    ...props
  }: React.ComponentProps<"button"> & {
    activityKey?: string | number;
    activityLabel?: (count: number) => React.ReactNode;
  }) => {
    void _activityKey;
    void _activityLabel;
    return (
      <button data-testid="scroll-to-latest" style={style} {...props}>
        {children}
      </button>
    );
  },
}));

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

const scrollIntoViewMock = vi.fn();

Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
  configurable: true,
  value: scrollIntoViewMock,
});

describe("MessageList scroll-to-latest affordance", () => {
  test("unmounts distant historical turn content until it nears the viewport", () => {
    let notify: IntersectionObserverCallback | null = null;
    class MockIntersectionObserver implements IntersectionObserver {
      readonly root = null;
      readonly rootMargin = "1200px 0px";
      readonly thresholds = [0];
      constructor(callback: IntersectionObserverCallback) {
        notify = callback;
      }
      disconnect() {}
      observe() {}
      takeRecords(): IntersectionObserverEntry[] {
        return [];
      }
      unobserve() {}
    }
    const originalIntersectionObserver = window.IntersectionObserver;
    window.IntersectionObserver = MockIntersectionObserver;
    try {
      const view = render(
        <HistoricalTurnBoundary cacheKey="thread:turn" virtualize>
          <span>heavy historical content</span>
        </HistoricalTurnBoundary>,
      );
      const boundary = view.container.firstElementChild;
      expect(boundary).toHaveAttribute("data-turn-mounted", "false");
      expect(screen.queryByText("heavy historical content")).toBeNull();

      act(() => {
        notify?.(
          [{ isIntersecting: true } as IntersectionObserverEntry],
          {} as IntersectionObserver,
        );
      });
      expect(boundary).toHaveAttribute("data-turn-mounted", "true");
      expect(screen.getByText("heavy historical content")).toBeInTheDocument();
    } finally {
      window.IntersectionObserver = originalIntersectionObserver;
    }
  });

  beforeEach(() => {
    scrollIntoViewMock.mockClear();
  });

  test("floats above the reserved input area", () => {
    const thread = mockThread();

    renderWithProviders(
      <SubtasksProvider>
        <ThreadProviders thread={thread}>
          <MessageList
            threadId="thread-1"
            thread={thread}
            paddingBottom={180}
          />
        </ThreadProviders>
      </SubtasksProvider>,
      { initialRoute: "/workspace/realtime/thread-1" },
    );

    const button = screen.getByTestId("scroll-to-latest");
    expect(button).toHaveTextContent("Latest");
    expect(button).toHaveAccessibleName("Back to latest message");
    expect(button).toHaveStyle({
      bottom: "calc(var(--chat-input-overlay-height, 160px) + 12px)",
    });
    expect(screen.getByTestId("conversation-bottom-safe-area")).toHaveStyle({
      height: "max(0px, calc(var(--chat-input-overlay-height, 180px) - 52px))",
    });
    expect(screen.getByTestId("conversation-bottom-safe-area")).toHaveAttribute(
      "data-clearance-mode",
      "composer-overlap-only",
    );
  });

  test("uses the measured composer height instead of preserving the fallback floor", () => {
    const thread = mockThread();

    renderWithProviders(
      <div
        style={
          { "--chat-input-overlay-height": "112px" } as React.CSSProperties
        }
      >
        <SubtasksProvider>
          <ThreadProviders thread={thread}>
            <MessageList
              threadId="thread-1"
              thread={thread}
              paddingBottom={180}
            />
          </ThreadProviders>
        </SubtasksProvider>
      </div>,
      { initialRoute: "/workspace/realtime/thread-1" },
    );

    expect(screen.getByTestId("conversation-bottom-safe-area")).toHaveStyle({
      height: "max(0px, calc(var(--chat-input-overlay-height, 180px) - 52px))",
    });
  });

  test("renders a left turn locator rail for quick jumps between user turns", () => {
    const messages: Message[] = [
      { id: "user-1", type: "human", content: "first request" },
      { id: "assistant-1", type: "ai", content: "first answer" },
      { id: "user-2", type: "human", content: "second request" },
      { id: "assistant-2", type: "ai", content: "second answer" },
    ];
    const thread = mockThread({ messages });

    renderWithProviders(
      <SubtasksProvider>
        <ThreadProviders thread={thread}>
          <MessageList threadId="thread-1" thread={thread} paddingBottom={0} />
        </ThreadProviders>
      </SubtasksProvider>,
      { initialRoute: "/workspace/realtime/thread-1" },
    );

    const rail = screen.getByRole("navigation", {
      name: "Turn locator",
    });
    expect(rail).toHaveClass("hidden", "md:block");
    expect(
      within(rail).getByRole("button", { name: /Turn 1/ }),
    ).toHaveAccessibleName(/first request/);
    expect(
      within(rail).getByRole("button", {
        name: "Jump to first turn",
      }),
    ).toBeInTheDocument();
    expect(
      within(rail).getByRole("button", {
        name: "Jump to last turn",
      }),
    ).toBeInTheDocument();

    fireEvent.click(within(rail).getByRole("button", { name: /Turn 2/ }));

    expect(scrollIntoViewMock).toHaveBeenCalledWith({
      behavior: "smooth",
      block: "start",
    });
  });

  test("display-locks only completed turns and keeps the newest turn active", () => {
    const messages: Message[] = [
      { id: "user-1", type: "human", content: "first request" },
      { id: "assistant-1", type: "ai", content: "first answer" },
      { id: "user-2", type: "human", content: "second request" },
      { id: "assistant-2", type: "ai", content: "streaming answer" },
    ];
    const assistant = messages[3]!;
    const thread = mockThread({
      messages,
      streamingMessage: assistant,
      isLoading: true,
    });

    const { container } = renderWithProviders(
      <SubtasksProvider>
        <ThreadProviders thread={thread}>
          <MessageList threadId="thread-1" thread={thread} paddingBottom={0} />
        </ThreadProviders>
      </SubtasksProvider>,
      { initialRoute: "/workspace/realtime/thread-1" },
    );

    const turns = container.querySelectorAll("[data-message-turn]");
    expect(turns).toHaveLength(2);
    expect(turns[0]).toHaveAttribute("data-turn-rendering", "history");
    expect(turns[0]).toHaveClass("message-turn-history");
    expect(turns[1]).toHaveAttribute("data-turn-rendering", "active");
    expect(turns[1]).not.toHaveClass("message-turn-history");
    expect(turns[1]).toContainElement(screen.getByText("streaming answer"));
  });

  test("partitions leading activity and human turns without dropping groups", () => {
    const grouped = groupMessages(
      [
        { id: "assistant-prelude", type: "ai", content: "restored context" },
        { id: "user-1", type: "human", content: "first request" },
        { id: "assistant-1", type: "ai", content: "first answer" },
        { id: "user-2", type: "human", content: "second request" },
      ],
      (group) => group,
    );

    const turns = partitionMessageGroupsIntoTurns(grouped);

    expect(turns.map((turn) => turn.groupIndexes)).toEqual([[0], [1, 2], [3]]);
    expect(turns[0]?.key).toMatch(/^prelude:/);
    expect(turns[1]?.key).toMatch(/^human:/);
  });

  test("builds locator markers from turn boundaries without including a prelude", () => {
    const grouped = groupMessages(
      [
        { id: "assistant-prelude", type: "ai", content: "restored context" },
        { id: "user-1", type: "human", content: "  first   request  " },
        {
          id: "assistant-1",
          type: "ai",
          content: "planned answer",
          additional_kwargs: { phase_id: "phase-1" },
        },
        { id: "user-2", type: "human", content: "" },
        { id: "assistant-2", type: "ai", content: "short answer" },
      ] as Message[],
      (group) => group,
    );
    const turns = partitionMessageGroupsIntoTurns(grouped);

    expect(
      buildTurnMarkers(grouped, turns, (number) => `Turn ${number}`),
    ).toEqual([
      {
        key: turns[1]!.key,
        kind: "phase",
        label: "first request",
        number: 1,
      },
      {
        key: turns[2]!.key,
        kind: "dot",
        label: "Turn 2",
        number: 2,
      },
    ]);
  });

  test("renders explicitly phased turns as quiet bars and short turns as dots", () => {
    const messages: Message[] = [
      { id: "user-1", type: "human", content: "large research task" },
      {
        id: "assistant-1",
        type: "ai",
        content: "Working through the plan.",
        additional_kwargs: {
          phase_id: "phase-1",
        },
      },
      { id: "user-2", type: "human", content: "quick follow-up" },
      { id: "assistant-2", type: "ai", content: "short answer" },
    ];
    const thread = mockThread({ messages });

    renderWithProviders(
      <SubtasksProvider>
        <ThreadProviders thread={thread}>
          <MessageList threadId="thread-1" thread={thread} paddingBottom={0} />
        </ThreadProviders>
      </SubtasksProvider>,
      { initialRoute: "/workspace/realtime/thread-1" },
    );

    const rail = screen.getByRole("navigation", {
      name: "Turn locator",
    });
    const phasedTurn = within(rail).getByRole("button", {
      name: /Turn 1/,
    });
    const shortTurn = within(rail).getByRole("button", {
      name: /Turn 2/,
    });

    expect(phasedTurn).toHaveAttribute("data-turn-marker-kind", "phase");
    expect(shortTurn).toHaveAttribute("data-turn-marker-kind", "dot");
    expect(phasedTurn.querySelector("span[aria-hidden='true']")).toHaveClass(
      "h-8",
    );
    expect(shortTurn.querySelector("span[aria-hidden='true']")).toHaveClass(
      "size-2.5",
    );
    expect(shortTurn).toHaveAttribute("data-turn-marker-active", "true");
    expect(
      shortTurn.querySelector("[data-turn-marker-avatar='true']"),
    ).toBeNull();
  });

  test("shows a workbench status light only on the running latest turn marker", () => {
    const messages: Message[] = [
      { id: "user-1", type: "human", content: "large research task" },
      {
        id: "assistant-1",
        type: "ai",
        content: "Phase 1: plan\nPhase 2: execute",
      },
      { id: "user-2", type: "human", content: "continue" },
      { id: "assistant-2", type: "ai", content: "working" },
    ];
    const runningEvent: LiveToolEvent = {
      id: "tool-1",
      name: "model_reasoning",
      status: "running",
      startedAt: 1,
      iteration: 1,
    };
    const thread = mockThread({ isLoading: true, messages });

    renderWithProviders(
      <SubtasksProvider>
        <ThreadProviders thread={thread}>
          <MessageList
            liveToolEvents={[runningEvent]}
            paddingBottom={0}
            thread={thread}
            threadId="thread-1"
          />
        </ThreadProviders>
      </SubtasksProvider>,
      { initialRoute: "/workspace/realtime/thread-1" },
    );

    const rail = screen.getByRole("navigation", {
      name: "Turn locator",
    });
    const firstTurn = within(rail).getByRole("button", {
      name: /Turn 1/,
    });
    const latestTurn = within(rail).getByRole("button", {
      name: /Turn 2/,
    });

    expect(firstTurn.querySelector("[data-turn-marker-status]")).toBeNull();
    expect(
      latestTurn.querySelector("[data-turn-marker-status='running']"),
    ).toBeTruthy();
  });

  test("does not infer phased locator bars from tool names or prose", () => {
    expect(
      turnMarkerKindFromMessages([
        { id: "user-1", type: "human", content: "第 2 阶段：实现界面" },
        {
          id: "assistant-1",
          type: "ai",
          content: "",
          tool_calls: [
            {
              id: "todo-1",
              name: "todo_write",
              args: {
                items: [
                  { content: "Plan UI", status: "completed" },
                  { content: "Implement", status: "in_progress" },
                ],
              },
            },
          ],
        },
      ]),
    ).toBe("dot");
  });

  test("uses explicit timeline coordinates for phased locator bars", () => {
    expect(
      turnMarkerKindFromMessages([
        { id: "user-1", type: "human", content: "build the whole feature" },
        {
          id: "assistant-1",
          type: "ai",
          content: "",
          tool_calls: [
            {
              id: "read-1",
              name: "read_file",
              args: { path: "src/app.tsx" },
              phaseId: "phase-2",
            },
          ],
        },
      ]),
    ).toBe("phase");
  });

  test("limits the locator rail to a sliding window near the active turn", () => {
    const markers = Array.from({ length: 24 }, (_, index) => ({
      key: `human:${index + 1}`,
      kind: "dot" as const,
      label: `turn ${index + 1}`,
      number: index + 1,
    }));

    const latestWindow = visibleTurnMarkerWindow(markers, null);
    expect(latestWindow).toHaveLength(17);
    expect(latestWindow[0]?.number).toBe(8);
    expect(latestWindow.at(-1)?.number).toBe(24);

    const middleWindow = visibleTurnMarkerWindow(markers, "human:10");
    expect(middleWindow).toHaveLength(17);
    expect(middleWindow.map((marker) => marker.number)).toEqual([
      1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17,
    ]);
  });

  test("chooses the turn containing the message viewport center", () => {
    const markers = [
      { key: "human:1", kind: "dot" as const, label: "one", number: 1 },
      { key: "human:2", kind: "phase" as const, label: "two", number: 2 },
      { key: "human:3", kind: "dot" as const, label: "three", number: 3 },
    ];

    expect(
      nearestTurnKeyByViewportCenter(
        markers,
        {
          "human:1": { top: -260, bottom: -20 },
          "human:2": { top: 40, bottom: 640 },
          "human:3": { top: 680, bottom: 900 },
        },
        0,
        720,
      ),
    ).toBe("human:2");
  });
});
