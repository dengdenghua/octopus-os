import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useRealtimeThread } from "./use-realtime-thread";

interface CapturedRequest {
  method: string;
  params: Record<string, unknown>;
}

function realtimeHarness() {
  const requests: CapturedRequest[] = [];
  let emitNotification!: (notification: {
    method: string;
    params: Record<string, unknown>;
  }) => void;
  const factory = (deps: {
    onNotification: (notification: {
      method: string;
      params: Record<string, unknown>;
    }) => void;
    onOpen?: () => void;
  }) => {
    emitNotification = deps.onNotification;
    return {
      connect: () => deps.onOpen?.(),
      close: () => {},
      notify: () => {},
      request: (method: string, params: Record<string, unknown>) => {
        requests.push({ method, params });
        if (method === "thread/resume") {
          return Promise.resolve({
            thread: { id: "thread-send-contract" },
            turns: [],
          });
        }
        return Promise.resolve({ accepted: true });
      },
    };
  };
  const rendered = renderHook(() =>
    useRealtimeThread({
      threadId: "thread-send-contract",
      clientFactory: factory as never,
    }),
  );
  return {
    rendered,
    requests,
    emitNotification: (note: Parameters<typeof emitNotification>[0]) =>
      emitNotification(note),
  };
}

describe("realtime outbound wire contract", () => {
  it("sends the stable new-turn coordinate as top-level userItemId", async () => {
    const { rendered, requests } = realtimeHarness();
    await waitFor(() =>
      expect(rendered.result.current.state.resumeState).toBe("resumed"),
    );

    await act(async () => {
      await rendered.result.current.startTurn({
        input: "先确认根因",
        clientItemId: "itm_user_wire_1",
        metadata: { context: { mode: "chat" } },
      });
    });

    const started = requests.find((request) => request.method === "turn/start");
    expect(started?.params).toMatchObject({
      threadId: "thread-send-contract",
      userItemId: "itm_user_wire_1",
      input: [
        {
          type: "text",
          text: "先确认根因",
          metadata: { context: { mode: "chat" } },
        },
      ],
    });
    expect(started?.params.input).toEqual([
      expect.not.objectContaining({ itemId: expect.anything() }),
    ]);
  });

  it("sends a selected project directory as the top-level execution cwd", async () => {
    const { rendered, requests } = realtimeHarness();
    await waitFor(() =>
      expect(rendered.result.current.state.resumeState).toBe("resumed"),
    );

    await act(async () => {
      await rendered.result.current.startTurn({
        input: "分析该项目",
        cwd: "/Users/example/project",
        metadata: {
          context: {
            mode: "code",
            workspace_path: "/Users/example/project",
            workspace_scope: "project",
          },
        },
      });
    });

    const started = requests.find((request) => request.method === "turn/start");
    expect(started?.params).toMatchObject({
      threadId: "thread-send-contract",
      cwd: "/Users/example/project",
      input: [
        {
          metadata: {
            context: {
              workspace_path: "/Users/example/project",
              workspace_scope: "project",
            },
          },
        },
      ],
    });
  });

  it("keeps running steering on the current turn and preserves its item id", async () => {
    const { rendered, requests, emitNotification } = realtimeHarness();
    await waitFor(() =>
      expect(rendered.result.current.state.resumeState).toBe("resumed"),
    );
    act(() => {
      emitNotification({
        method: "turn/started",
        params: {
          threadId: "thread-send-contract",
          turn: {
            id: "turn-running",
            threadId: "thread-send-contract",
            status: "inProgress",
            items: [],
            startedAt: "2026-08-22T12:00:00.000Z",
            completedAt: null,
            error: null,
          },
        },
      });
    });

    await act(async () => {
      await rendered.result.current.steer({
        input: "顺便检查许可证",
        itemId: "itm_user_steer_wire_1",
      });
    });

    expect(
      requests.filter((request) => request.method === "turn/start"),
    ).toHaveLength(0);
    expect(
      requests.find((request) => request.method === "turn/steer")?.params,
    ).toEqual({
      threadId: "thread-send-contract",
      turnId: "turn-running",
      itemId: "itm_user_steer_wire_1",
      text: "顺便检查许可证",
    });
  });
});
