import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  activateTimelineItem,
  clearTimelineHighlight,
  findTimelineItemElement,
  getTimelineLinkageState,
  initialTimelineLinkageState,
  reduceActivateTimelineItem,
  reduceClearTimelineHighlight,
  resetTimelineLinkage,
  subscribeTimelineLinkage,
  TIMELINE_HIGHLIGHT_DURATION_MS,
} from "./timeline-linkage";

// ─── 纯函数（reducer） ───────────────────────────────────────────

describe("reduceActivateTimelineItem", () => {
  it("记录激活 id 与来源，并递增 nonce", () => {
    const next = reduceActivateTimelineItem(
      initialTimelineLinkageState,
      "item-1",
      "chat",
    );
    expect(next.activeTimelineItemId).toBe("item-1");
    expect(next.activeSource).toBe("chat");
    expect(next.nonce).toBe(1);
  });

  it("来源为 sidebar 时设置高亮 id（对话区需要短暂高亮）", () => {
    const next = reduceActivateTimelineItem(
      initialTimelineLinkageState,
      "item-2",
      "sidebar",
    );
    expect(next.highlightedTimelineItemId).toBe("item-2");
  });

  it("来源为 chat 时不高亮（点击项自身已可见），并清掉上一轮高亮", () => {
    const sidebarActivated = reduceActivateTimelineItem(
      initialTimelineLinkageState,
      "item-2",
      "sidebar",
    );
    const next = reduceActivateTimelineItem(sidebarActivated, "item-3", "chat");
    expect(next.highlightedTimelineItemId).toBeNull();
  });

  it("重复激活同一条目仍递增 nonce（视为新意图）", () => {
    const first = reduceActivateTimelineItem(
      initialTimelineLinkageState,
      "item-1",
      "sidebar",
    );
    const second = reduceActivateTimelineItem(first, "item-1", "sidebar");
    expect(second.nonce).toBe(first.nonce + 1);
    expect(second.highlightedTimelineItemId).toBe("item-1");
  });

  it("空 id 不产生任何状态变化（返回原引用）", () => {
    const state = reduceActivateTimelineItem(
      initialTimelineLinkageState,
      "item-1",
      "chat",
    );
    expect(reduceActivateTimelineItem(state, "", "chat")).toBe(state);
    expect(reduceActivateTimelineItem(state, "   ", "sidebar")).toBe(state);
  });

  it("id 会去除首尾空白", () => {
    const next = reduceActivateTimelineItem(
      initialTimelineLinkageState,
      "  item-1  ",
      "chat",
    );
    expect(next.activeTimelineItemId).toBe("item-1");
  });
});

describe("reduceClearTimelineHighlight", () => {
  it("清除高亮但保留激活 id 与来源", () => {
    const activated = reduceActivateTimelineItem(
      initialTimelineLinkageState,
      "item-1",
      "sidebar",
    );
    const cleared = reduceClearTimelineHighlight(activated);
    expect(cleared.highlightedTimelineItemId).toBeNull();
    expect(cleared.activeTimelineItemId).toBe("item-1");
    expect(cleared.activeSource).toBe("sidebar");
    expect(cleared.nonce).toBe(activated.nonce);
  });

  it("无高亮时返回原引用（幂等）", () => {
    expect(reduceClearTimelineHighlight(initialTimelineLinkageState)).toBe(
      initialTimelineLinkageState,
    );
  });
});

// ─── 模块级 store：高亮生命周期（2s 自动清除） ─────────────────────

describe("timeline linkage store", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetTimelineLinkage();
  });

  afterEach(() => {
    resetTimelineLinkage();
    vi.useRealTimers();
  });

  it("激活后通知订阅者", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeTimelineLinkage(listener);
    activateTimelineItem("item-1", "chat");
    expect(listener).toHaveBeenCalledTimes(1);
    expect(getTimelineLinkageState().activeTimelineItemId).toBe("item-1");
    unsubscribe();
    activateTimelineItem("item-2", "chat");
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("sidebar 激活产生的高亮在 2s 后自动清除", () => {
    activateTimelineItem("item-1", "sidebar");
    expect(getTimelineLinkageState().highlightedTimelineItemId).toBe("item-1");
    vi.advanceTimersByTime(TIMELINE_HIGHLIGHT_DURATION_MS - 1);
    expect(getTimelineLinkageState().highlightedTimelineItemId).toBe("item-1");
    vi.advanceTimersByTime(1);
    expect(getTimelineLinkageState().highlightedTimelineItemId).toBeNull();
    // 激活 id 保留，便于两侧继续定位
    expect(getTimelineLinkageState().activeTimelineItemId).toBe("item-1");
  });

  it("2s 内再次激活会重置高亮计时（高亮跟随最新条目）", () => {
    activateTimelineItem("item-1", "sidebar");
    vi.advanceTimersByTime(TIMELINE_HIGHLIGHT_DURATION_MS - 500);
    activateTimelineItem("item-2", "sidebar");
    vi.advanceTimersByTime(TIMELINE_HIGHLIGHT_DURATION_MS - 1);
    expect(getTimelineLinkageState().highlightedTimelineItemId).toBe("item-2");
    vi.advanceTimersByTime(1);
    expect(getTimelineLinkageState().highlightedTimelineItemId).toBeNull();
  });

  it("chat 激活会立即取消上一轮高亮定时器", () => {
    activateTimelineItem("item-1", "sidebar");
    activateTimelineItem("item-2", "chat");
    expect(getTimelineLinkageState().highlightedTimelineItemId).toBeNull();
    vi.advanceTimersByTime(TIMELINE_HIGHLIGHT_DURATION_MS * 2);
    // 过期定时器不会误清后续状态
    expect(getTimelineLinkageState().activeTimelineItemId).toBe("item-2");
    expect(getTimelineLinkageState().highlightedTimelineItemId).toBeNull();
  });

  it("clearTimelineHighlight 可手动清除并取消定时器", () => {
    activateTimelineItem("item-1", "sidebar");
    clearTimelineHighlight();
    expect(getTimelineLinkageState().highlightedTimelineItemId).toBeNull();
    vi.advanceTimersByTime(TIMELINE_HIGHLIGHT_DURATION_MS * 2);
    expect(getTimelineLinkageState().highlightedTimelineItemId).toBeNull();
  });

  it("带 nonce 的 clearTimelineHighlight 在 nonce 不匹配时不生效", () => {
    activateTimelineItem("item-1", "sidebar");
    const nonce = getTimelineLinkageState().nonce;
    activateTimelineItem("item-2", "sidebar");
    clearTimelineHighlight(nonce);
    expect(getTimelineLinkageState().highlightedTimelineItemId).toBe("item-2");
  });

  it("resetTimelineLinkage 恢复初始状态", () => {
    activateTimelineItem("item-1", "sidebar");
    resetTimelineLinkage();
    expect(getTimelineLinkageState()).toEqual(initialTimelineLinkageState);
  });
});

// ─── DOM 定位：共享 id + lane 限定 ───────────────────────────────

describe("findTimelineItemElement", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("同一 id 两侧各有一个元素时，按 lane 命中对应侧（与文档顺序无关）", () => {
    // 对话区在文档中先出现：不限定 lane 会误中对话区行
    document.body.innerHTML = `
      <div data-timeline-item-id="item-1" data-timeline-lane="chat"></div>
      <button data-timeline-item-id="item-1" data-timeline-lane="sidebar"></button>
    `;
    const chatEl = findTimelineItemElement("item-1", "chat");
    const sidebarEl = findTimelineItemElement("item-1", "sidebar");
    expect(chatEl?.dataset.timelineLane).toBe("chat");
    expect(sidebarEl?.dataset.timelineLane).toBe("sidebar");
    expect(chatEl).not.toBe(sidebarEl);
  });

  it("本侧没有匹配元素时返回 null（联动退化为静默不定位）", () => {
    document.body.innerHTML = `
      <div data-timeline-item-id="item-1" data-timeline-lane="chat"></div>
    `;
    expect(findTimelineItemElement("item-1", "sidebar")).toBeNull();
    expect(findTimelineItemElement("item-1", "chat")).not.toBeNull();
  });

  it("空 id 与不存在 id 返回 null", () => {
    document.body.innerHTML = `
      <div data-timeline-item-id="item-1" data-timeline-lane="chat"></div>
    `;
    expect(findTimelineItemElement("", "chat")).toBeNull();
    expect(findTimelineItemElement("   ", "chat")).toBeNull();
    expect(findTimelineItemElement("missing", "chat")).toBeNull();
  });

  it("id 含引号等特殊字符时选择器安全转义", () => {
    const trickyId = 'call_"quoted"\\id';
    const el = document.createElement("div");
    el.dataset.timelineItemId = trickyId;
    el.dataset.timelineLane = "chat";
    document.body.appendChild(el);
    expect(findTimelineItemElement(trickyId, "chat")).toBe(el);
  });
});
