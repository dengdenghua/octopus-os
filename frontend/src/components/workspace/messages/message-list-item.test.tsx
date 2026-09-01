import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Message } from "@/core/api/types";
import { describe, expect, it, vi } from "vitest";
import { RETRY_PENDING_MESSAGE_EVENT } from "@/core/threads/optimistic-messages";

import {
  containsProtocolMarkers,
  HumanMessageDeliveryStatus,
  messageClipboardText,
  ShadowReviewAction,
  threadMessageToCoworkRoomMessage,
} from "./message-list-item";

const evolutionMocks = vi.hoisted(() => ({
  getStatus: vi.fn(),
  queueRun: vi.fn(),
}));

vi.mock("@/core/evolution/api", () => ({
  getDualHelixShadowStatus: evolutionMocks.getStatus,
  queueDualHelixShadowRun: evolutionMocks.queueRun,
}));

vi.mock("@/providers/AuthProvider", () => ({
  useAuth: () => ({
    authStatus: { enabled: false },
    user: null,
  }),
  useOptionalAuth: () => ({
    authStatus: { enabled: false },
    user: null,
  }),
}));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      conversation: {
        messageQueued: "排队中",
        messageSending: "发送中",
        messageSendFailed: "发送失败",
        retry: "重试",
      },
    },
  }),
}));

describe("HumanMessageDeliveryStatus", () => {
  it("makes queued websocket delivery explicit", () => {
    render(
      <HumanMessageDeliveryStatus
        threadId="thread-queued"
        message={{
          id: "itm_user_queued",
          type: "human",
          content: "等连接恢复",
          additional_kwargs: { delivery_state: "queued" },
        }}
      />,
    );

    expect(screen.getByRole("status")).toHaveAttribute(
      "data-delivery-state",
      "queued",
    );
    expect(screen.getByText("排队中")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
  });

  it("dispatches retry with the failed bubble's stable message id", () => {
    const retryListener = vi.fn();
    window.addEventListener(RETRY_PENDING_MESSAGE_EVENT, retryListener);
    render(
      <HumanMessageDeliveryStatus
        threadId="thread-failed"
        message={{
          id: "itm_user_failed",
          type: "human",
          content: "保留这条消息",
          additional_kwargs: {
            delivery_state: "failed",
            delivery_error: "socket dropped",
          },
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    expect(retryListener).toHaveBeenCalledTimes(1);
    expect((retryListener.mock.calls[0]?.[0] as CustomEvent).detail).toEqual({
      threadId: "thread-failed",
      clientMessageId: "itm_user_failed",
    });
    window.removeEventListener(RETRY_PENDING_MESSAGE_EVENT, retryListener);
  });
});

describe("protocol cleaning bail-out", () => {
  it("does not skip the bare legacy sub-agent placeholder", () => {
    // The legacy placeholder may appear WITHOUT the optional `[...]`
    // prefix; its ASCII `(` must therefore be a first-mark so the
    // streaming fast path still runs the cleaning chain.
    expect(
      containsProtocolMarkers("(sub-agent exceeded token budget 100/200)"),
    ).toBe(true);
  });

  it("empties a bare legacy sub-agent placeholder from clipboard text", () => {
    const message = {
      type: "ai",
      content: "(sub-agent exceeded token budget 100/200)",
    } as unknown as Message;
    expect(messageClipboardText(message)).toBe("");
  });

  it("still skips plain prose without protocol markers", () => {
    expect(
      containsProtocolMarkers("这是一段普通的流式回答，没有任何协议标记。"),
    ).toBe(false);
    expect(containsProtocolMarkers("plain english prose here")).toBe(false);
  });
});

describe("project group message mirror", () => {
  it("uses the canonical thread message id as an idempotent room source", () => {
    const message = {
      id: "human-42",
      type: "human",
      content: "请把这项工作交给 @agent:planner",
    } as Message;

    expect(
      threadMessageToCoworkRoomMessage(message, "thread-1", 3, undefined),
    ).toMatchObject({
      seq: -1,
      text: "请把这项工作交给 @agent:planner",
      metadata: { source_message_id: "thread:human-42" },
    });
  });

  it("hydrates prior project receipts from the hidden room mirror", () => {
    const message = {
      id: "human-42",
      type: "human",
      content: "确定采用 A 方案",
    } as Message;
    const metadata = {
      source_message_id: "thread:human-42",
      project_actions: [
        {
          id: "action-1",
          action: "record_decision" as const,
          project_id: "project-1",
          target: { kind: "decision", id: "decision-1" },
        },
      ],
    };

    expect(
      threadMessageToCoworkRoomMessage(message, "thread-1", 3, {
        "thread:human-42": metadata,
      }).metadata,
    ).toBe(metadata);
  });
});

describe("ShadowReviewAction", () => {
  it("queues the opposite-engine review only after an explicit click", async () => {
    evolutionMocks.getStatus.mockResolvedValue({
      ok: true,
      enabled: true,
      runs: [],
    });
    evolutionMocks.queueRun.mockResolvedValue({
      run_id: "shadow-1",
      goal: "修复问题",
      primary_engine: "echo",
      shadow_engine: "codex",
      status: "queued",
      created_at: "2026-08-23T00:00:00Z",
      source_thread_id: "thread-1",
      source_message_id: "answer-1",
    });

    render(
      <ShadowReviewAction
        context={{
          goal: "修复问题",
          primaryEngine: "echo",
          primaryOutput: "已经修复",
          threadId: "thread-1",
          messageId: "answer-1",
          workspacePath: "/workspace/project",
        }}
      />,
    );

    expect(evolutionMocks.queueRun).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "让另一引擎复核本次任务" }),
    );

    await waitFor(() =>
      expect(evolutionMocks.queueRun).toHaveBeenCalledWith({
        goal: "修复问题",
        primary_engine: "echo",
        primary_output: "已经修复",
        workspace_path: "/workspace/project",
        source_thread_id: "thread-1",
        source_message_id: "answer-1",
      }),
    );
    expect(
      screen.getByRole("button", { name: "另一引擎正在影子复核" }),
    ).toBeDisabled();
  });
});
