import { describe, expect, test, vi } from "vitest";

import {
  AGENT_WORKBENCH_OPEN_EVENT,
  emitOpenAgentWorkbench,
  sanitizeWorkbenchOpenDetail,
} from "./agent-workbench-events";

describe("agent workbench open events", () => {
  test("sanitizes process event snapshots before they reach the sidebar", () => {
    const detail = sanitizeWorkbenchOpenDetail({
      tab: "agent",
      eventId: "event-1",
      eventKind: "execution",
      view: "trace",
      processEvent: {
        kind: "execution",
        status: "done",
        count: 1,
        summary:
          "Action: read_file <read_only> </read_only> Bearer abcdefghijklmnop",
        detail:
          "<ToolCallBlock>private args token=super-secret</ToolCallBlock>\n已确认公开事实。\nread_file",
      },
    });

    expect(JSON.stringify(detail.processEvent)).not.toMatch(
      /read_only|ToolCallBlock|read_file|Bearer abcdef|super-secret/i,
    );
    expect(detail.processEvent?.summary).toBe("已确认公开事实。");
    expect(detail.processEvent?.detail).toBe("已确认公开事实。\noperation");
  });

  test("falls back to a neutral label only when no public detail survives", () => {
    const detail = sanitizeWorkbenchOpenDetail({
      processEvent: {
        kind: "execution",
        summary: "Action: read_file",
        detail:
          "<ToolCallBlock>private args token=super-secret</ToolCallBlock>",
      },
    });

    expect(detail.processEvent?.summary).toBe("…");
    expect(detail.processEvent?.detail).toBe("…");
  });

  test("emits the sanitized detail, not the caller's raw payload", () => {
    const handler = vi.fn();
    window.addEventListener(AGENT_WORKBENCH_OPEN_EVENT, handler);

    emitOpenAgentWorkbench({
      tab: "agent",
      processEvent: {
        kind: "thinking",
        summary: "<TextBlock>我先核对。</TextBlock>",
        detail: "Thought: hidden\nFinal Answer: 我先核对。",
      },
    });

    const event = handler.mock.calls.at(-1)?.[0] as CustomEvent;
    expect(event.detail.processEvent.summary).toBe("我先核对。");
    expect(event.detail.processEvent.detail).toBe("我先核对。");
    window.removeEventListener(AGENT_WORKBENCH_OPEN_EVENT, handler);
  });
});
