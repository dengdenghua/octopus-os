import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  type UseRealtimeThreadValue,
  useRealtimeThread,
} from "@/core/realtime";
import type {
  Conversation,
  Item,
  SteeringUserMessageItem,
  Turn,
  UserMessageItem,
} from "@/core/realtime/items";
import { emptyConversation } from "@/core/realtime/items";
import { emptyVitals } from "@/core/realtime/stream-vitals";

import { RETRY_PENDING_MESSAGE_EVENT } from "./optimistic-messages";
import { useThreadStreamRealtime } from "./use-thread-stream-realtime";

vi.mock("@/core/realtime", () => ({ useRealtimeThread: vi.fn() }));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      chatInputBox: { uploadFailed: "Upload failed" },
      conversation: {
        previousMessagePending: "Previous message is still pending",
      },
    },
  }),
}));

const CREATED_AT = "2026-08-22T12:00:00.000Z";

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function userItem(id: string, text: string): UserMessageItem {
  return {
    id,
    type: "userMessage",
    status: "completed",
    createdAt: CREATED_AT,
    text,
    attachments: [],
  };
}

function steeringItem(
  id: string,
  text: string,
  turnId: string,
): SteeringUserMessageItem {
  return {
    id,
    type: "steeringUserMessage",
    status: "completed",
    createdAt: CREATED_AT,
    text,
    targetTurnId: turnId,
    source: "user",
  };
}

function turn(
  id: string,
  items: Item[],
  status: Turn["status"] = "inProgress",
): Turn {
  return {
    id,
    threadId: "thread-send-regression",
    status,
    startedAt: CREATED_AT,
    completedAt: status === "inProgress" ? null : CREATED_AT,
    items,
    error: null,
  };
}

function conversation(turns: Turn[]): Conversation {
  return {
    ...emptyConversation("thread-send-regression"),
    resumeState: "resumed",
    turns,
  };
}

function realtimeValue(overrides: Partial<UseRealtimeThreadValue> = {}) {
  const value: UseRealtimeThreadValue = {
    state: conversation([]),
    connected: true,
    startTurn: vi.fn().mockResolvedValue(undefined),
    steer: vi.fn().mockResolvedValue(undefined),
    resolveApproval: vi.fn(),
    vitals: emptyVitals(),
    resume: vi.fn().mockResolvedValue(undefined),
    loadOlderTurns: vi.fn().mockResolvedValue(undefined),
    interrupt: vi.fn().mockResolvedValue(undefined),
    compact: vi.fn().mockResolvedValue({ compacted: false }),
    decideHunk: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
  vi.mocked(useRealtimeThread).mockImplementation(() => value);
  return value;
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("useThreadStreamRealtime outbound regression", () => {
  it("renders a new message immediately and reconciles reconnect history to one row", async () => {
    const request = deferred<void>();
    const startTurn = vi.fn(() => request.promise);
    const realtime = realtimeValue({ startTurn });
    const rendered = renderHook(() =>
      useThreadStreamRealtime({ threadId: "thread-send-regression" }),
    );

    act(() => {
      rendered.result.current[1]("thread-send-regression", {
        text: "先确认根因",
        files: [],
      });
    });

    await waitFor(() =>
      expect(rendered.result.current[0].messages).toHaveLength(1),
    );
    const optimistic = rendered.result.current[0].messages[0]!;
    expect(optimistic).toMatchObject({
      type: "human",
      content: "先确认根因",
      additional_kwargs: { delivery_state: "sending" },
    });
    expect(startTurn).toHaveBeenCalledWith(
      expect.objectContaining({ clientItemId: optimistic.id }),
    );

    realtime.connected = false;
    realtime.state = {
      ...conversation([]),
      resumeState: "needsResume",
    };
    rendered.rerender();
    await waitFor(() =>
      expect(
        rendered.result.current[0].messages[0]?.additional_kwargs
          ?.delivery_state,
      ).toBe("queued"),
    );

    realtime.connected = true;
    realtime.state = conversation([
      turn("turn-new", [userItem(String(optimistic.id), "先确认根因")]),
    ]);
    rendered.rerender();
    await waitFor(() => {
      expect(rendered.result.current[0].messages).toHaveLength(1);
      expect(
        rendered.result.current[0].messages[0]?.additional_kwargs
          ?.delivery_state,
      ).toBeUndefined();
    });
    expect(rendered.result.current[0].messages[0]?.id).toBe(optimistic.id);

    request.resolve();
    await act(async () => request.promise);
  });

  it("adds one pending steering row without starting a second turn", async () => {
    const request = deferred<void>();
    const steer = vi.fn(() => request.promise);
    const startTurn = vi.fn().mockResolvedValue(undefined);
    const original = userItem("itm_user_original", "开始检查");
    const realtime = realtimeValue({
      state: conversation([turn("turn-running", [original])]),
      startTurn,
      steer,
    });
    const rendered = renderHook(() =>
      useThreadStreamRealtime({ threadId: "thread-send-regression" }),
    );

    act(() => {
      rendered.result.current[1]("thread-send-regression", {
        text: "顺便检查许可证",
        files: [],
      });
    });

    await waitFor(() =>
      expect(rendered.result.current[0].messages).toHaveLength(2),
    );
    const optimistic = rendered.result.current[0].messages.at(-1)!;
    expect(startTurn).not.toHaveBeenCalled();
    expect(steer).toHaveBeenCalledWith({
      input: "顺便检查许可证",
      itemId: optimistic.id,
    });

    realtime.state = conversation([
      turn("turn-running", [
        original,
        steeringItem(String(optimistic.id), "顺便检查许可证", "turn-running"),
      ]),
    ]);
    rendered.rerender();
    await waitFor(() =>
      expect(rendered.result.current[0].messages).toHaveLength(2),
    );
    expect(
      rendered.result.current[0].messages.filter(
        (message) => message.id === optimistic.id,
      ),
    ).toHaveLength(1);

    request.resolve();
    await act(async () => request.promise);
  });

  it("keeps a pre-start failure retryable and retries with the same id", async () => {
    const retry = deferred<void>();
    const startTurn = vi
      .fn()
      .mockRejectedValueOnce(new Error("socket closed before turn/started"))
      .mockImplementationOnce(() => retry.promise);
    const realtime = realtimeValue({ startTurn });
    const rendered = renderHook(() =>
      useThreadStreamRealtime({ threadId: "thread-send-regression" }),
    );

    act(() => {
      rendered.result.current[1]("thread-send-regression", {
        text: "不要丢失这条消息",
        files: [],
      });
    });

    await waitFor(() =>
      expect(
        rendered.result.current[0].messages[0]?.additional_kwargs
          ?.delivery_state,
      ).toBe("failed"),
    );
    const clientItemId = String(rendered.result.current[0].messages[0]?.id);
    expect(rendered.result.current[0].messages).toHaveLength(1);

    act(() => {
      window.dispatchEvent(
        new CustomEvent(RETRY_PENDING_MESSAGE_EVENT, {
          detail: {
            threadId: "thread-send-regression",
            clientMessageId: clientItemId,
          },
        }),
      );
    });
    await waitFor(() => expect(startTurn).toHaveBeenCalledTimes(2));
    expect(startTurn.mock.calls.map((call) => call[0].clientItemId)).toEqual([
      clientItemId,
      clientItemId,
    ]);
    expect(
      rendered.result.current[0].messages[0]?.additional_kwargs?.delivery_state,
    ).toBe("sending");

    realtime.state = conversation([
      turn("turn-retry", [userItem(clientItemId, "不要丢失这条消息")]),
    ]);
    rendered.rerender();
    await waitFor(() =>
      expect(rendered.result.current[0].messages).toHaveLength(1),
    );
    expect(
      rendered.result.current[0].messages[0]?.additional_kwargs?.delivery_state,
    ).toBeUndefined();

    retry.resolve();
    await act(async () => retry.promise);
  });
});
