import { describe, expect, it } from "vitest";

import type {
  TimelineItem,
  ToolCallTimelineItem,
} from "@/components/workspace/messages/message-group";
import {
  buildCompactTimelineItems,
  capCompactTimelineItems,
  isExecutionTimelineItem,
} from "./timeline-pipeline";

function toolCall(
  id: string,
  name: string,
  args: Record<string, unknown> = {},
): ToolCallTimelineItem {
  return {
    id,
    type: "toolCall",
    step: { id, type: "toolCall", name, args, iteration: 1 },
  };
}

function commentary(id: string, text: string): TimelineItem {
  return {
    id,
    type: "commentary",
    step: { id, type: "commentary", commentary: text, iteration: 1 },
  };
}

describe("timeline-pipeline", () => {
  it("aggregates consecutive similar tool calls and keeps original references", () => {
    const search1 = toolCall("s1", "web_search", { query: "a" });
    const search2 = toolCall("s2", "web_search", { query: "b" });
    const items = [search1, search2];

    const compact = buildCompactTimelineItems(items, new Map());

    expect(compact).toHaveLength(1);
    const group = compact[0]!;
    expect(group.type).toBe("aggregatedToolGroup");
    if (group.type !== "aggregatedToolGroup") return;
    expect(group.count).toBe(2);
    // 聚合组按 step id 映射回原始 ToolCallTimelineItem 引用，
    // 工作台据此恢复证据行。
    expect(group.items[0]).toBe(search1);
    expect(group.items[1]).toBe(search2);
  });

  it("retains tool calls whose effect receipt is indeterminate", () => {
    const indeterminate = toolCall("s1", "web_search", { query: "a" });
    indeterminate.step.effectReceipt = {
      state: "indeterminate",
    } as ToolCallTimelineItem["step"]["effectReceipt"];
    const items = [indeterminate, commentary("c1", "已确认方向")];

    const compact = buildCompactTimelineItems(items, new Map());

    const retained = compact.some((item) => item === indeterminate);
    expect(retained).toBe(true);
  });

  it("passes short mixed sequences through by reference", () => {
    const note = commentary("c1", "开始处理");
    const call = toolCall("s1", "read_file", { path: "a.ts" });
    const items = [note, call];

    const compact = buildCompactTimelineItems(items, new Map());

    expect(compact).toHaveLength(2);
    // 短序列不采样不聚合：原对象引用直接进入输出。
    expect(compact[0]).toBe(note);
    expect(compact[1]).toBe(call);
    expect(isExecutionTimelineItem(call)).toBe(true);
    expect(isExecutionTimelineItem(note)).toBe(false);
  });

  it("never aggregates tools across a narrative checkpoint hidden by compaction", () => {
    const items: TimelineItem[] = [];
    for (let index = 0; index < 8; index += 1) {
      items.push(commentary(`c${index}`, `阶段 ${index} 已确认`));
      items.push(
        toolCall(`t${index}`, "exec_shell", {
          command: `check-${index}`,
        }),
      );
    }

    const compact = buildCompactTimelineItems(items, new Map());

    // Long-run sampling hides some commentary rows. Those hidden rows still
    // delimit execution segments: every original tool had prose before the
    // next one, so none may become one cross-checkpoint aggregate.
    expect(compact.some((item) => item.type === "aggregatedToolGroup")).toBe(
      false,
    );
    expect(compact.filter((item) => item.type === "toolCall")).toHaveLength(8);
  });

  it("bounds alternating long-run process DOM while preserving chronology and uncertainty", () => {
    const items: TimelineItem[] = Array.from({ length: 180 }, (_, index) =>
      toolCall(`t${index}`, index % 2 === 0 ? "read_file" : "web_search", {
        index,
      }),
    );
    const uncertain = items[91] as ToolCallTimelineItem;
    uncertain.step.effectReceipt = {
      state: "indeterminate",
    } as ToolCallTimelineItem["step"]["effectReceipt"];
    items.splice(30, 0, commentary("c1", "阶段检查"));
    items.splice(120, 0, commentary("c2", "最终核验"));

    const compact = capCompactTimelineItems(items);

    expect(compact.length).toBeLessThanOrEqual(48);
    expect(compact[0]).toBe(items[0]);
    expect(compact.at(-1)).toBe(items.at(-1));
    expect(compact).toContain(uncertain);
    expect(compact).toContain(items.find((item) => item.id === "c1"));
    expect(compact).toContain(items.find((item) => item.id === "c2"));
    const sourcePositions = compact.map((item) => items.indexOf(item));
    expect(sourcePositions).toEqual([...sourcePositions].sort((a, b) => a - b));
  });

  it("applies the DOM cap after narrative checkpoints defeat tool aggregation", () => {
    const items: TimelineItem[] = [];
    for (let index = 0; index < 120; index += 1) {
      items.push(commentary(`c${index}`, `checkpoint ${index}`));
      items.push(
        toolCall(`t${index}`, index % 2 === 0 ? "read_file" : "web_search", {
          index,
        }),
      );
    }

    const compact = buildCompactTimelineItems(items, new Map());

    expect(compact.length).toBeLessThanOrEqual(48);
    expect(compact.some((item) => item.id === "t0")).toBe(true);
    expect(compact.some((item) => item.id === "t119")).toBe(true);
    expect(
      compact.filter((item) => item.type === "commentary").length,
    ).toBeGreaterThan(0);
  });
});
