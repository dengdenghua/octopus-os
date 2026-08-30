import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AIMessage } from "@/core/api/types";
import { ToolEffectsProvider } from "@/core/observability/tool-effects-context";
import { renderWithProviders } from "@/test/harness";

import {
  AGENT_WORKBENCH_OPEN_EVENT,
  type AgentWorkbenchOpenDetail,
} from "../agent-workbench-events";
import { MessageGroup } from "./message-group";

const snapshot = {
  backend: "redis",
  shared_across_hosts: true,
  can_authorize_retry: true,
  count: 0,
  state_counts: {},
  receipts: [],
};

describe("MessageGroup external-effect receipts", () => {
  afterEach(() => vi.restoreAllMocks());

  it("marks only the matching tool call and opens its receipt in the workbench", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(snapshot), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const message: AIMessage = {
      id: "message-1",
      type: "ai",
      content: "",
      tool_calls: [
        {
          id: "call-risk",
          name: "write_file",
          args: { path: "result.txt", content: "done" },
          effectReceipt: {
            effectKey: "effect:risky-write",
            callId: "call-risk",
            state: "indeterminate",
            reason: "remote write outcome unknown",
            fencingToken: 9,
          },
        },
        ...Array.from({ length: 6 }, (_, index) => ({
          id: `call-safe-${index}`,
          name: "read_file",
          args: { path: `source-${index}.txt` },
        })),
      ],
    };
    let opened: AgentWorkbenchOpenDetail | null = null;
    const listener = (event: Event) => {
      opened = (event as CustomEvent<AgentWorkbenchOpenDetail>).detail;
    };
    window.addEventListener(AGENT_WORKBENCH_OPEN_EVENT, listener, {
      once: true,
    });

    renderWithProviders(
      <ToolEffectsProvider>
        <MessageGroup keepOpen messages={[message]} />
      </ToolEffectsProvider>,
      { locale: "zh-CN" },
    );

    const badge = await screen.findByTestId("tool-effect-review-badge");
    expect(screen.getAllByTestId("tool-effect-review-badge")).toHaveLength(1);
    expect(badge).toHaveTextContent("需核对");
    const row = badge.closest("button");
    expect(row).not.toBeNull();
    fireEvent.click(row!);

    expect(opened).toMatchObject({
      eventId: "call-risk",
      eventKind: "execution",
      effectKey: "effect:risky-write",
      view: "trace",
    });
  });
});
