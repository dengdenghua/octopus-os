/**
 * 时间线压缩管道（纯函数模块，无 React 依赖）。
 *
 * 输入 convertToSteps → groupConsecutiveReasoningSteps 产出的完整
 * TimelineItem[]，输出可直接渲染的紧凑时间线：
 *
 *   1. selectCompactTimelineItems — 语义保真采样（每轮 intent + 最新
 *      fact 锚点，短执行序列全保留）
 *   2. retainIndeterminateToolCalls — 效果不确定的工具调用永不折叠
 *   3. aggregateSimilarToolCalls — 相邻同类工具调用聚合（引用保持，
 *      映射回原始 ToolCallTimelineItem 供工作台恢复证据）
 *
 * 每一级都是纯函数，可独立单测；组合入口为 buildCompactTimelineItems。
 */
import type {
  ActionCallbackGroupItem,
  AggregatedToolGroupTimelineItem,
  CommentaryTimelineItem,
  TimelineItem,
  ToolCallTimelineItem,
} from "./message-group";
import {
  aggregateSimilarToolCalls,
  isAggregatedToolGroup,
} from "./activity-aggregator";
import {
  assignTimelineRoles,
  type RoleAssignableStep,
  type TimelineRole,
} from "./timeline-role";

const MAX_PUBLIC_PROGRESS_ANCHORS = 4;
// 语义保底（每轮 intent + 最新 fact）超出基础额度时，commentary 总额放宽到的上限
const MAX_SEMANTIC_PROGRESS_ANCHORS = 6;
// The conversation lane is a narrative, not the full event database. A long
// task can alternate hundreds of tool kinds, defeating same-kind aggregation
// and crashing Chromium with a huge latest-turn DOM. Full events remain in
// the workbench; this cap keeps representative chronological anchors here.
const MAX_COMPACT_TIMELINE_ITEMS = 48;

export function isExecutionTimelineItem(
  item: TimelineItem,
): item is
  | ToolCallTimelineItem
  | ActionCallbackGroupItem
  | AggregatedToolGroupTimelineItem {
  return (
    item.type === "toolCall" ||
    item.type === "actionCallbackGroup" ||
    item.type === "aggregatedToolGroup"
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

// activity-aggregator describes timeline items with structural (duck) types
// to avoid a circular import; the aggregator preserves input objects by
// reference, so these guards can safely narrow its output back to the local
// TimelineItem types at the boundary.
export function isToolCallTimelineItem(
  value: unknown,
): value is ToolCallTimelineItem {
  if (!isRecord(value) || value.type !== "toolCall") return false;
  if (typeof value.id !== "string" || !isRecord(value.step)) return false;
  return typeof value.step.name === "string" && isRecord(value.step.args);
}

export function isTimelineItem(value: unknown): value is TimelineItem {
  if (isToolCallTimelineItem(value)) return true;
  if (!isRecord(value)) return false;
  if (value.type === "reasoningGroup" || value.type === "actionCallbackGroup") {
    return Array.isArray(value.steps);
  }
  return value.type === "commentary" && isRecord(value.step);
}

/** 条目所属轮次：缺失 iteration 的旧数据归第 1 轮。 */
function timelineItemIteration(item: TimelineItem): number {
  if (item.type === "reasoningGroup" || item.type === "actionCallbackGroup") {
    return item.steps[0]?.iteration ?? 1;
  }
  if (item.type === "aggregatedToolGroup") {
    return item.items[item.items.length - 1]?.step.iteration ?? 1;
  }
  return item.step.iteration ?? 1;
}

/** 条目在角色推断视角下的最小步骤形状（与 RoleAssignableStep 结构兼容）。 */
function roleAssignableViewOf(item: TimelineItem): RoleAssignableStep {
  if (item.type === "reasoningGroup" || item.type === "actionCallbackGroup") {
    return (
      item.steps[0] ?? {
        type: item.type === "reasoningGroup" ? "reasoning" : "actionCallback",
      }
    );
  }
  if (item.type === "aggregatedToolGroup") {
    return (
      item.items[0]?.step ?? {
        type: "toolCall" as const,
        name: "",
        args: {},
      }
    );
  }
  return item.step;
}

/**
 * 解析每个条目的语义角色。
 * 优先沿用条目自带 role；兼容 role 为 undefined 的旧数据时，用
 * assignTimelineRoles 在判定副本上补齐 —— 选择器返回的仍是原 item 引用，
 * 不破坏下游 React memo 的引用相等。
 */
function resolveTimelineItemRoles(
  items: TimelineItem[],
): Map<TimelineItem, TimelineRole | undefined> {
  const roles = new Map<TimelineItem, TimelineRole | undefined>();
  if (!items.some((item) => item.role === undefined)) {
    for (const item of items) roles.set(item, item.role);
    return roles;
  }
  const assigned = assignTimelineRoles(items.map(roleAssignableViewOf));
  items.forEach((item, index) => {
    roles.set(item, item.role ?? assigned[index]?.role);
  });
  return roles;
}

/**
 * 语义感知采样（长任务）：
 * - 每个 iteration 必留 ≥1 个 intent 条目（该轮首个 intent 角色的
 *   commentary / reasoningGroup；该轮无 intent 角色条目则按位置取首个
 *   commentary）；
 * - 全部条目里最新一个 fact 条目必留（无 fact 角色条目时跳过）；
 * - 剩余 commentary 名额按原有均匀采样补足，保底超额时总额放宽到
 *   MAX_SEMANTIC_PROGRESS_ANCHORS。
 */
function representativeNarrativeAnchors(
  items: TimelineItem[],
  commentary: CommentaryTimelineItem[],
  roles: Map<TimelineItem, TimelineRole | undefined>,
): {
  anchors: Set<TimelineItem>;
  visibleCommentary: CommentaryTimelineItem[];
} {
  const anchors = new Set<TimelineItem>();

  // 按轮分组叙事条目，逐轮保底 intent 锚点
  const narrativeByIteration = new Map<number, TimelineItem[]>();
  for (const item of items) {
    if (item.type !== "commentary" && item.type !== "reasoningGroup") continue;
    const iteration = timelineItemIteration(item);
    const group = narrativeByIteration.get(iteration);
    if (group) {
      group.push(item);
    } else {
      narrativeByIteration.set(iteration, [item]);
    }
  }
  for (const group of narrativeByIteration.values()) {
    const intentAnchor =
      group.find((item) => roles.get(item) === "intent") ??
      group.find((item) => item.type === "commentary");
    if (intentAnchor) anchors.add(intentAnchor);
  }

  // 最新一个 fact 条目必留
  const lastFact = [...items]
    .reverse()
    .find((item) => roles.get(item) === "fact");
  if (lastFact) anchors.add(lastFact);

  // 剩余 commentary 名额按均匀采样补足；保底超额时不再追加采样
  const guaranteedCount = commentary.filter((item) => anchors.has(item)).length;
  const budget = Math.min(
    Math.max(MAX_PUBLIC_PROGRESS_ANCHORS, guaranteedCount),
    MAX_SEMANTIC_PROGRESS_ANCHORS,
  );
  const remainingSlots = budget - guaranteedCount;
  if (remainingSlots > 0) {
    const candidates = commentary.filter((item) => !anchors.has(item));
    if (candidates.length <= remainingSlots) {
      candidates.forEach((item) => anchors.add(item));
    } else {
      const lastIndex = candidates.length - 1;
      for (let slot = 0; slot < remainingSlots; slot += 1) {
        const index = Math.round(
          remainingSlots === 1
            ? lastIndex / 2
            : (slot * lastIndex) / (remainingSlots - 1),
        );
        anchors.add(candidates[index]!);
      }
    }
  }
  return {
    anchors,
    visibleCommentary: commentary.filter((item) => anchors.has(item)),
  };
}

// 导出供单测直接触达（渲染层行为不变）
export function selectCompactTimelineItems(
  items: TimelineItem[],
): TimelineItem[] {
  const commentary = items.filter((item) => item.type === "commentary");
  const executionCount = items.filter(isExecutionTimelineItem).length;
  // Short tool runs are still a conversation, not a log archive. Keep their
  // complete causal sequence so the aggregator can present one faithful
  // summary row and the Workbench can recover every evidence reference.
  if (commentary.length === 0 && executionCount > 0 && executionCount <= 12) {
    return items;
  }
  const latestThinking = [...items]
    .reverse()
    .find((item) => item.type === "reasoningGroup");
  const selected = new Set<TimelineItem>();
  // Thinking is a timeline event, not a status widget. Earlier reasoning must
  // stay on the lane at the position it happened, otherwise the transcript
  // reads as one perpetually-latest thought window instead of
  // "thought → did → said → thought".
  let visibleCommentary: CommentaryTimelineItem[];
  if (commentary.length <= MAX_PUBLIC_PROGRESS_ANCHORS) {
    // 短对话：行为完全不变，commentary 全量保留
    visibleCommentary = commentary;
  } else {
    // 长任务：语义保真采样，保证每轮意图与最新事实不被均匀采样裁掉。
    // 采样基于语义角色与轮次位置，不依赖模型措辞或硬编码阶段名；
    // 完整事件链仍可在工作台查看。
    const result = representativeNarrativeAnchors(
      items,
      commentary,
      resolveTimelineItemRoles(items),
    );
    result.anchors.forEach((item) => selected.add(item));
    visibleCommentary = result.visibleCommentary;
  }
  visibleCommentary.forEach((item) => selected.add(item));
  if (latestThinking) selected.add(latestThinking);
  // Preserve every execution that falls inside a visible conversational
  // interval. Consecutive same-kind calls are folded by the aggregator below,
  // so the transcript still reads as "said → did → said → did" while the
  // workbench can recover every evidence reference.
  const visibleCommentaryIndexes = visibleCommentary
    .map((item) => items.indexOf(item))
    .filter(
      (index, position, indexes) =>
        index >= 0 && indexes.indexOf(index) === position,
    )
    .sort((a, b) => a - b);
  const boundaries = [-1, ...visibleCommentaryIndexes, items.length];
  for (
    let boundaryIndex = 0;
    boundaryIndex < boundaries.length - 1;
    boundaryIndex += 1
  ) {
    const start = boundaries[boundaryIndex]! + 1;
    const end = boundaries[boundaryIndex + 1]!;
    const intervalItems = items
      .slice(start, end)
      .filter(
        (item) =>
          isExecutionTimelineItem(item) || item.type === "reasoningGroup",
      );
    for (const intervalItem of intervalItems) {
      selected.add(intervalItem);
    }
  }
  return items.filter((item) => selected.has(item));
}

export function retainIndeterminateToolCalls(
  timelineItems: TimelineItem[],
  compactItems: TimelineItem[],
  receiptsByCallId: ReadonlyMap<string, { state: string }>,
): TimelineItem[] {
  const selected = new Set(compactItems);
  return timelineItems.filter(
    (item) =>
      selected.has(item) ||
      (item.type === "toolCall" &&
        Boolean(item.step.id) &&
        (item.step.effectReceipt?.state === "indeterminate" ||
          receiptsByCallId.get(item.step.id!)?.state === "indeterminate")),
  );
}

function hasIndeterminateEffect(item: TimelineItem): boolean {
  if (item.type === "toolCall") {
    return item.step.effectReceipt?.state === "indeterminate";
  }
  if (item.type === "aggregatedToolGroup") {
    return item.items.some(
      (child) => child.step.effectReceipt?.state === "indeterminate",
    );
  }
  return false;
}

/** Bound the latest turn's rendered process DOM without discarding evidence. */
export function capCompactTimelineItems(items: TimelineItem[]): TimelineItem[] {
  if (items.length <= MAX_COMPACT_TIMELINE_ITEMS) return items;

  const requiredIndexes = new Set<number>();
  items.forEach((item, index) => {
    // Public narration/reasoning is already semantically sampled upstream.
    // Keep all of it, plus effect-uncertain writes that require user review.
    if (!isExecutionTimelineItem(item) || hasIndeterminateEffect(item)) {
      requiredIndexes.add(index);
    }
  });

  const candidates = items
    .map((_item, index) => index)
    .filter((index) => !requiredIndexes.has(index));
  const slots = Math.max(0, MAX_COMPACT_TIMELINE_ITEMS - requiredIndexes.size);
  if (slots > 0 && candidates.length > 0) {
    const last = candidates.length - 1;
    for (let slot = 0; slot < Math.min(slots, candidates.length); slot += 1) {
      const candidateIndex = Math.round(
        slots === 1 ? last : (slot * last) / (slots - 1),
      );
      requiredIndexes.add(candidates[candidateIndex]!);
    }
  }
  return items.filter((_item, index) => requiredIndexes.has(index));
}

/**
 * 管道组合入口：采样 → 保留不确定调用 → 聚合同类调用。
 * 聚合结果按 step id 映射回原始 ToolCallTimelineItem（聚合器保持输入
 * 引用，但 duck 类型需要收窄回 TimelineItem）。
 */
export function buildCompactTimelineItems(
  timelineItems: TimelineItem[],
  receiptsByCallId: ReadonlyMap<string, { state: string }>,
): TimelineItem[] {
  const selected = retainIndeterminateToolCalls(
    timelineItems,
    // The main conversation keeps the latest public thought and latest action.
    // Earlier process events remain in the right workbench. This is structural
    // and independent of model wording, language, or hard-coded phase names.
    selectCompactTimelineItems(timelineItems),
    receiptsByCallId,
  );
  // Build index for quick lookup of original ToolCallTimelineItem by step id
  const toolItemById = new Map<string, ToolCallTimelineItem>();
  for (const item of selected) {
    if (item.type === "toolCall" && item.step.id) {
      toolItemById.set(item.step.id, item);
    }
  }
  // Apply activity aggregation without erasing causal boundaries. Selection
  // intentionally hides some narrative anchors on long runs; aggregating the
  // already-filtered array would make tools on opposite sides of a hidden
  // checkpoint look consecutive and fold them into one misleading block.
  // Walk the original timeline and flush at every non-tool item — selected or
  // hidden — so one visible receipt can never cross prose/reasoning that
  // actually occurred between its calls.
  const selectedSet = new Set(selected);
  const aggregated: ReturnType<typeof aggregateSimilarToolCalls> = [];
  let toolRun: ToolCallTimelineItem[] = [];
  const flushToolRun = () => {
    if (toolRun.length === 0) return;
    aggregated.push(
      ...aggregateSimilarToolCalls(toolRun, {
        groupMixedKinds: true,
        groupAcrossPhases: true,
      }),
    );
    toolRun = [];
  };
  for (const item of timelineItems) {
    if (item.type !== "toolCall") {
      flushToolRun();
      if (selectedSet.has(item)) aggregated.push(item);
      continue;
    }
    if (selectedSet.has(item)) toolRun.push(item);
  }
  flushToolRun();
  const compact = aggregated.map((item): TimelineItem => {
    if (isAggregatedToolGroup(item)) {
      const mappedItems: ToolCallTimelineItem[] = [];
      for (const toolLike of item.items) {
        const stepId = toolLike.step.id;
        if (stepId) {
          const original = toolItemById.get(stepId);
          if (original) {
            mappedItems.push(original);
            continue;
          }
        }
        // Aggregation preserves the original object. Falling back by tool
        // name maps repeated anonymous calls to the wrong evidence row.
        if (isToolCallTimelineItem(toolLike)) {
          mappedItems.push(toolLike);
        }
      }
      return {
        id: item.id,
        type: "aggregatedToolGroup",
        aggregateKind: item.aggregateKind,
        count: item.count,
        phaseId: item.phaseId,
        items:
          mappedItems.length > 0
            ? mappedItems
            : item.items.filter(isToolCallTimelineItem),
        role: item.role as TimelineRole | undefined,
        inferred: item.inferred,
      };
    }
    if (isTimelineItem(item)) return item;
    // The aggregator passes non-aggregated items through by reference, so
    // every item here originates from the TimelineItem[] above. Reaching
    // this branch means the input was corrupted upstream.
    throw new TypeError(
      "aggregateSimilarToolCalls returned an unknown timeline item",
    );
  });
  return capCompactTimelineItems(compact);
}
