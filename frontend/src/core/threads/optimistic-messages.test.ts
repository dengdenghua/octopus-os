import { describe, expect, it } from "vitest";

import type { HumanMessage, Message } from "@/core/api/types";

import {
  acknowledgedClientMessageIds,
  mergeOptimisticHumanMessages,
  optimisticMessageReducer,
  pendingOutboundToHumanMessage,
  type PendingOutboundMessage,
} from "./optimistic-messages";

function pending(
  overrides: Partial<PendingOutboundMessage> = {},
): PendingOutboundMessage {
  return {
    clientMessageId: "itm_user_client_1",
    threadId: "thread-send-contract",
    intent: "start",
    message: { text: "先确认根因", files: [] },
    displayText: "先确认根因",
    createdAt: "2026-08-22T12:00:00.000Z",
    deliveryState: "sending",
    ...overrides,
  };
}

function serverHuman(
  id: string,
  content: string,
  additionalKwargs: HumanMessage["additional_kwargs"] = {},
): HumanMessage {
  return {
    id,
    type: "human",
    content,
    additional_kwargs: additionalKwargs,
  };
}

describe("optimistic realtime message reconciliation", () => {
  it("shows an outbound message before the server receipt exists", () => {
    const outbound = pending();
    const state = optimisticMessageReducer([], {
      type: "enqueue",
      message: outbound,
    });

    expect(mergeOptimisticHumanMessages([], state)).toEqual([
      expect.objectContaining({
        id: outbound.clientMessageId,
        type: "human",
        content: "先确认根因",
        additional_kwargs: expect.objectContaining({
          delivery_state: "sending",
        }),
      }),
    ]);
  });

  it("replaces the optimistic new-turn row with one canonical user message", () => {
    const outbound = pending();
    const canonical = serverHuman(
      outbound.clientMessageId,
      outbound.displayText,
    );

    const visible = mergeOptimisticHumanMessages([canonical], [outbound]);

    expect(visible).toEqual([canonical]);
    expect(visible.filter((message) => message.type === "human")).toHaveLength(
      1,
    );
  });

  it("deduplicates a running-turn steering receipt by the same item id", () => {
    const original = serverHuman("itm_user_original", "开始检查");
    const outbound = pending({
      clientMessageId: "itm_user_steer_1",
      message: { text: "顺便检查许可证", files: [] },
      displayText: "顺便检查许可证",
    });
    const steering = serverHuman(
      outbound.clientMessageId,
      outbound.displayText,
      {
        steering: true,
        target_turn_id: "turn-running",
      },
    );

    const visible = mergeOptimisticHumanMessages(
      [original, steering],
      [outbound],
    );

    expect(visible.map((message) => message.id)).toEqual([
      "itm_user_original",
      "itm_user_steer_1",
    ]);
    expect(visible).toHaveLength(2);
  });

  it("keeps a failed row retryable and reuses its stable id", () => {
    const outbound = pending();
    const failed = optimisticMessageReducer([outbound], {
      type: "set-delivery",
      clientMessageId: outbound.clientMessageId,
      deliveryState: "failed",
      error: "socket closed before turn/started",
    });

    const failedHuman = pendingOutboundToHumanMessage(failed[0]!);
    expect(failedHuman.id).toBe(outbound.clientMessageId);
    expect(failedHuman.additional_kwargs).toMatchObject({
      delivery_state: "failed",
      retryable: true,
      delivery_error: "socket closed before turn/started",
    });

    const retried = optimisticMessageReducer(failed, {
      type: "set-delivery",
      clientMessageId: outbound.clientMessageId,
      deliveryState: "sending",
    });
    expect(retried).toEqual([
      expect.objectContaining({
        clientMessageId: outbound.clientMessageId,
        deliveryState: "sending",
        error: undefined,
      }),
    ]);
  });

  it("drops pending state after resume observes the canonical server id", () => {
    const outbound = pending({ deliveryState: "queued" });
    const resumedMessages: Message[] = [
      serverHuman(outbound.clientMessageId, outbound.displayText),
    ];
    const acknowledged = acknowledgedClientMessageIds(resumedMessages);

    const reconciled = optimisticMessageReducer([outbound], {
      type: "acknowledge",
      clientMessageIds: acknowledged,
    });

    expect(acknowledged).toEqual(new Set([outbound.clientMessageId]));
    expect(reconciled).toEqual([]);
  });

  it("keeps the same array identity when an acknowledge drops nothing", () => {
    const outbound = pending({ deliveryState: "sending" });
    const state = [outbound];

    const unrelated = optimisticMessageReducer(state, {
      type: "acknowledge",
      clientMessageIds: new Set(["some-other-message"]),
    });

    expect(unrelated).toBe(state);
  });
});
