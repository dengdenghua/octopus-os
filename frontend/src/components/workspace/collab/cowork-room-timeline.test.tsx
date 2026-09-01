import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";

import type { CoworkRoomMessage } from "@/core/cowork";
import { renderWithProviders } from "@/test/harness";

import { buildCoworkMessageProjectActionInput } from "./cowork-room-message-actions";
import {
  CoworkRoomTimeline,
  CoworkRoomTimelineEntry,
  dedupeCoworkRoomMessages,
} from "./cowork-room-timeline";

const messages: CoworkRoomMessage[] = [
  {
    seq: 4,
    participant_id: "planner",
    display_name: "规划师",
    text: "请 @agent:researcher 完成竞品调研",
    ts: "2026-08-21T01:00:00Z",
    metadata: {
      entity_refs: [{ kind: "milestone", id: "M-1", label: "调研阶段" }],
    },
  },
  {
    seq: 5,
    participant_id: "project-os",
    display_name: "Project OS",
    text: "已创建事项",
    metadata: {
      message_type: "system_card",
      system_card: {
        type: "create_item",
        title: "已创建事项 · 完成竞品调研",
        summary: "来自群聊消息",
        status: "pending",
        target: { kind: "task", id: "PT-1", label: "完成竞品调研" },
      },
    },
  },
];

describe("CoworkRoomTimeline", () => {
  test("renders member mentions, entity refs and Project OS system cards", async () => {
    const user = userEvent.setup();
    const onEntityClick = vi.fn();
    renderWithProviders(
      <CoworkRoomTimeline
        messages={messages}
        participants={[
          { id: "planner", display_name: "规划师", kind: "agent" },
          { id: "researcher", display_name: "研究员", kind: "agent" },
        ]}
        onEntityClick={onEntityClick}
      />,
      { locale: "zh-CN" },
    );

    expect(screen.getByText("@研究员")).toHaveAttribute(
      "title",
      "@agent:researcher",
    );
    expect(screen.getByText("已创建事项 · 完成竞品调研")).toBeInTheDocument();
    expect(screen.getByTestId("cowork-system-card")).toHaveAttribute(
      "data-density",
      "compact",
    );

    await user.click(screen.getByRole("button", { name: "完成竞品调研" }));
    expect(onEntityClick).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "task", id: "PT-1" }),
    );
  });

  test("builds a create-item action and assigns the first mentioned agent", () => {
    expect(
      buildCoworkMessageProjectActionInput("create_item", messages[0], {
        projectId: "P-1",
        milestoneId: "M-1",
      }),
    ).toEqual(
      expect.objectContaining({
        action: "create_item",
        project_id: "P-1",
        milestone_id: "M-1",
        assigned_agent: "researcher",
        title: "请 @agent:researcher 完成竞品调研",
      }),
    );
  });

  test("embeds one room event without creating a nested log", () => {
    renderWithProviders(
      <div role="log" aria-label="统一群聊时间线">
        <CoworkRoomTimelineEntry
          message={messages[0]}
          participants={[{ id: "planner", display_name: "规划师" }]}
        />
      </div>,
      { locale: "zh-CN" },
    );

    expect(screen.getAllByRole("log")).toHaveLength(1);
    expect(screen.getByText("规划师")).toBeInTheDocument();
  });

  test("removes thread mirrors and repeated producer source ids", () => {
    expect(
      dedupeCoworkRoomMessages([
        {
          seq: 1,
          text: "线程镜像",
          metadata: { source_message_id: "thread:human-1" },
        },
        {
          seq: 2,
          text: "项目卡",
          metadata: {
            source_message_id: "project-action:1",
            message_type: "system_card",
          },
        },
        {
          seq: 3,
          text: "重复项目卡",
          metadata: { source_message_id: "project-action:1" },
        },
        { seq: 4, text: "无来源的房间消息" },
      ]).map((message) => message.text),
    ).toEqual(["项目卡", "无来源的房间消息"]);
  });
});
