/**
 * 对话区 ↔ 侧边栏 双向联动的共享状态（threads 域）。
 *
 * 单一数据源原则：这里只保存被激活时间线条目的共享 id 与激活来源，
 * 不复制任何时间线数据。对话区时间线项与侧边栏条目通过同一个
 * ``data-timeline-item-id`` 属性在 DOM 中互相定位。
 *
 * 与 ``sidebar.ts`` 一样，本文件的 reducer 部分保持纯函数（无 React、
 * 无 DOM），可独立单测；模块级 store 通过订阅模式暴露，React 侧用
 * ``useSyncExternalStore(subscribeTimelineLinkage, getTimelineLinkageState)``
 * 接入。高亮生命周期（2s 自动清除）由 store 统一管理，避免多个消费
 * 组件各自持有定时器产生竞态。
 */

/** 激活来源侧：chat = 对话区时间线项点击，sidebar = 侧边栏条目点击 */
export type TimelineLinkageSource = "chat" | "sidebar";

export interface TimelineLinkageState {
  /** 当前被激活的时间线条目共享 id（任一侧点击产生） */
  activeTimelineItemId: string | null;
  /** 需要在另一侧短暂高亮的条目 id（2s 后自动清除） */
  highlightedTimelineItemId: string | null;
  /** 最后一次激活的来源侧 */
  activeSource: TimelineLinkageSource | null;
  /** 单调递增序号：重复激活同一条目也能让监听方识别为新意图 */
  nonce: number;
}

/** 高亮保留时长：克制的一次性提示，随后恢复 */
export const TIMELINE_HIGHLIGHT_DURATION_MS = 2000;

/** 两侧条目共用的 DOM 定位属性名 */
export const TIMELINE_ITEM_ID_ATTR = "data-timeline-item-id";

/** 条目所在侧（chat = 对话区，sidebar = 侧边栏工作台）的 DOM 标记属性名 */
export const TIMELINE_ITEM_LANE_ATTR = "data-timeline-lane";

/** 高亮样式 class（globals.css 中以 CSS transition 实现，无循环动画） */
export const TIMELINE_ITEM_HIGHLIGHT_CLASS = "timeline-item-linkage-highlight";

export const initialTimelineLinkageState: TimelineLinkageState = {
  activeTimelineItemId: null,
  highlightedTimelineItemId: null,
  activeSource: null,
  nonce: 0,
};

// ─── 纯函数（reducer，可独立单测） ───────────────────────────────

/**
 * 激活某个时间线条目。
 * - 来源为 sidebar 时：对话区需要滚动定位 + 短暂高亮，因此设置高亮 id；
 * - 来源为 chat 时：点击条目本身已可见，无需高亮对话区，仅记录激活。
 */
export function reduceActivateTimelineItem(
  state: TimelineLinkageState,
  id: string,
  source: TimelineLinkageSource,
): TimelineLinkageState {
  const itemId = id.trim();
  if (!itemId) return state;
  return {
    activeTimelineItemId: itemId,
    highlightedTimelineItemId: source === "sidebar" ? itemId : null,
    activeSource: source,
    nonce: state.nonce + 1,
  };
}

/** 清除高亮（保留激活 id 与来源）；已无高亮时返回原状态保证引用不变 */
export function reduceClearTimelineHighlight(
  state: TimelineLinkageState,
): TimelineLinkageState {
  if (state.highlightedTimelineItemId === null) return state;
  return { ...state, highlightedTimelineItemId: null };
}

// ─── DOM 定位（SSR 安全） ────────────────────────────────────────

function escapeSelectorValue(value: string): string {
  const escape = globalThis.CSS?.escape;
  if (typeof escape === "function") return escape(value);
  return value.replace(/["\\]/g, "\\$&");
}

/**
 * 按共享 id 查找时间线条目元素。两侧条目共用同一 id，因此必须传入
 * lane 限定查找侧：对话区与侧边栏各自只在自己一侧定位，避免命中
 * 文档中先出现的另一侧元素而滚错位置。
 */
export function findTimelineItemElement(
  id: string,
  lane: TimelineLinkageSource,
): HTMLElement | null {
  if (typeof document === "undefined") return null;
  const itemId = id.trim();
  if (!itemId) return null;
  return document.querySelector<HTMLElement>(
    `[${TIMELINE_ITEM_ID_ATTR}="${escapeSelectorValue(itemId)}"][${TIMELINE_ITEM_LANE_ATTR}="${lane}"]`,
  );
}

// ─── 模块级共享 store ────────────────────────────────────────────

let linkageState: TimelineLinkageState = initialTimelineLinkageState;
const linkageListeners = new Set<() => void>();
let highlightTimer: number | null = null;
/** 定时器对应的激活序号：竞态下只清除最后一次激活产生的高亮 */
let highlightTimerNonce = 0;

function emitLinkageChange() {
  for (const listener of linkageListeners) listener();
}

function cancelHighlightTimer() {
  if (highlightTimer === null) return;
  window.clearTimeout(highlightTimer);
  highlightTimer = null;
}

export function getTimelineLinkageState(): TimelineLinkageState {
  return linkageState;
}

export function subscribeTimelineLinkage(listener: () => void): () => void {
  linkageListeners.add(listener);
  return () => {
    linkageListeners.delete(listener);
  };
}

/** 激活时间线条目（任一侧点击时调用）；高亮 2s 后由 store 自动清除 */
export function activateTimelineItem(
  id: string,
  source: TimelineLinkageSource,
): void {
  const next = reduceActivateTimelineItem(linkageState, id, source);
  if (next === linkageState) return;
  linkageState = next;
  cancelHighlightTimer();
  if (
    next.highlightedTimelineItemId !== null &&
    typeof window !== "undefined"
  ) {
    highlightTimerNonce = next.nonce;
    highlightTimer = window.setTimeout(() => {
      highlightTimer = null;
      clearTimelineHighlight(highlightTimerNonce);
    }, TIMELINE_HIGHLIGHT_DURATION_MS);
  }
  emitLinkageChange();
}

/**
 * 清除高亮。传入 expectedNonce 时仅当 store 仍停留在该次激活才清除，
 * 防止过期的定时器误清后续激活产生的新高亮。
 */
export function clearTimelineHighlight(expectedNonce?: number): void {
  if (expectedNonce !== undefined && expectedNonce !== linkageState.nonce) {
    return;
  }
  cancelHighlightTimer();
  const next = reduceClearTimelineHighlight(linkageState);
  if (next === linkageState) return;
  linkageState = next;
  emitLinkageChange();
}

/** 测试专用：重置 store 并清理未决定时器 */
export function resetTimelineLinkage(): void {
  cancelHighlightTimer();
  linkageState = initialTimelineLinkageState;
  emitLinkageChange();
}
