import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CoworkRoomTimelineEntry } from "@/components/workspace/collab";
import type { BaseStream } from "@/core/api/use-stream-types";
import type { AgentThreadState } from "@/core/threads";
import { SubtasksProvider } from "@/core/tasks/context";
import { renderWithProviders } from "@/test/harness";

import { ThreadProviders } from "./context";
import {
  MessageList,
  placeTimelineEntries,
  type MessageListTimelineEntry,
} from "./message-list";

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
  useLocalSettings: () => [{ display: { chat_font_size: "medium" } }, vi.fn()],
}));

function entry(id: string, createdAt?: string): MessageListTimelineEntry {
  return { id, createdAt, content: id };
}

describe("placeTimelineEntries", () => {
  it("interleaves room events at thread-group boundaries in timestamp order", () => {
    const slots = placeTimelineEntries(
      ["2026-08-21T01:00:00Z", "2026-08-21T01:02:00Z", "2026-08-21T01:04:00Z"],
      [
        entry("after", "2026-08-21T01:05:00Z"),
        entry("between", "2026-08-21T01:03:00Z"),
        entry("before", "2026-08-21T00:59:00Z"),
      ],
    );

    expect(slots.map((slot) => slot.map((item) => item.id))).toEqual([
      ["before"],
      [],
      ["between"],
      ["after"],
    ]);
  });

  it("keeps stable ordering and puts undated events at the tail", () => {
    const slots = placeTimelineEntries(
      ["2026-08-21T01:00:00Z"],
      [
        entry("undated"),
        entry("same-a", "2026-08-21T01:00:00Z"),
        entry("same-b", "2026-08-21T01:00:00Z"),
      ],
    );

    expect(slots.map((slot) => slot.map((item) => item.id))).toEqual([
      ["same-a", "same-b"],
      ["undated"],
    ]);
  });

  it("suppresses the empty state for a room-only system card", () => {
    const thread: BaseStream<AgentThreadState> = {
      messages: [],
      streamingMessage: null,
      subgraphStreams: {},
      values: { title: "", messages: [], artifacts: [] },
      isLoading: false,
      isThreadLoading: false,
      error: undefined,
      stop: vi.fn(),
      refresh: vi.fn(),
      submit: vi.fn(),
      threadId: "thread-room-only",
    };
    const roomCard = (
      <CoworkRoomTimelineEntry
        message={{
          seq: 7,
          text: "已创建事项",
          metadata: {
            source_message_id: "project-action:7",
            message_type: "system_card",
            system_card: {
              type: "create_item",
              title: "已创建事项 · 发布检查清单",
            },
          },
        }}
      />
    );

    renderWithProviders(
      <SubtasksProvider>
        <ThreadProviders thread={thread}>
          <MessageList
            threadId="thread-room-only"
            thread={thread}
            paddingBottom={0}
            emptyState={<div>还没有消息</div>}
            timelineEntries={[
              { id: "room:7", createdAt: null, content: roomCard },
            ]}
          />
        </ThreadProviders>
      </SubtasksProvider>,
      { locale: "zh-CN", initialRoute: "/workspace/realtime/thread-room-only" },
    );

    expect(screen.getByText("已创建事项 · 发布检查清单")).toBeInTheDocument();
    expect(screen.queryByText("还没有消息")).not.toBeInTheDocument();
    expect(screen.getAllByRole("log")).toHaveLength(1);
  });

  it("keeps the recoverable group empty state inside the single central timeline", () => {
    const thread: BaseStream<AgentThreadState> = {
      messages: [],
      streamingMessage: null,
      subgraphStreams: {},
      values: { title: "发布讨论群", messages: [], artifacts: [] },
      isLoading: false,
      isThreadLoading: false,
      error: undefined,
      stop: vi.fn(),
      refresh: vi.fn(),
      submit: vi.fn(),
      threadId: "thread-empty-work-group",
    };

    renderWithProviders(
      <SubtasksProvider>
        <ThreadProviders thread={thread}>
          <MessageList
            threadId="thread-empty-work-group"
            thread={thread}
            paddingBottom={0}
            showSenderName
            emptyState={<div role="status">还没有消息，可以直接开始讨论</div>}
            timelineEntries={[]}
          />
        </ThreadProviders>
      </SubtasksProvider>,
      {
        locale: "zh-CN",
        initialRoute: "/workspace/realtime/thread-empty-work-group",
      },
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      "还没有消息，可以直接开始讨论",
    );
    expect(
      document.querySelector('[data-density="compact"]'),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("log")).toHaveLength(1);
  });
});
