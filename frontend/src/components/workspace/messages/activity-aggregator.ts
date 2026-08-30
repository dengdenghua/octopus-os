/**
 * 同类动作聚合器（纯函数模块，无 React 依赖）。
 *
 * 将 TimelineItem 序列中连续的同类型工具调用聚合成组。
 * 聚合规则：
 * - 同一 aggregateKind 且同一 phase 的连续 toolCall → 聚合为一组
 * - 非 toolCall 项打断聚合
 * - 单条不聚合，保持原样
 *
 * 注意：此模块使用结构化类型（duck typing）兼容 message-group.tsx 中的 TimelineItem，
 * 不直接导入该文件的类型以避免循环依赖。
 */
import type { ActionAggregateKind } from "./action-display";
import { getActionDisplay } from "./action-display";
import { shellCommandFromInput } from "../tool-name-groups";

interface ToolCallLike {
  type: "toolCall";
  step: {
    name: string;
    args: Record<string, unknown>;
    phaseId?: string;
    id?: string;
    messageId?: string;
    result?: unknown;
    role?: string;
    inferred?: boolean;
  };
  id?: string;
  role?: string;
  inferred?: boolean;
}

interface NonToolCallLike {
  type: string;
}

type TimelineItemLike = ToolCallLike | NonToolCallLike;

export type { TimelineItemLike };

export interface AggregatedToolGroupItem {
  type: "aggregatedToolGroup";
  id: string;
  aggregateKind: ActionAggregateKind;
  count: number;
  phaseId?: string;
  items: ToolCallLike[];
  role?: string;
  inferred?: boolean;
}

export interface ActivityAggregationOptions {
  /**
   * Fold adjacent tools from the same phase even when their visual kinds
   * differ. The conversation timeline uses this to read like one human
   * activity receipt; detailed per-tool evidence remains expandable.
   */
  groupMixedKinds?: boolean;
  /** Treat phase changes as internal detail within the same visible receipt. */
  groupAcrossPhases?: boolean;
}

export type MaybeAggregatedTimelineItem =
  | TimelineItemLike
  | AggregatedToolGroupItem;

const SHELL_FILE_READING_COMMANDS = new Set([
  "cat",
  "sed",
  "head",
  "tail",
  "grep",
  "awk",
  "cut",
  "sort",
  "uniq",
  "md5sum",
  "sha256sum",
  "wc",
  "less",
  "more",
  "file",
  // Some agents use cp/mv to a temp path as a read-workaround; the concrete
  // source file is the useful evidence, so fold these into the file cluster.
  "cp",
  "mv",
]);

function shellCommandFirstWord(command: string): string | undefined {
  return command
    .trim()
    .split(/\s+/)[0]
    ?.toLowerCase()
    .replace(/^["']+|["']+$/g, "");
}

function isFileReadingShellCommand(item: ToolCallLike): boolean {
  const cmd = shellCommandFromInput(item.step.args, item.step.name);
  if (!cmd) return false;
  const first = shellCommandFirstWord(cmd);
  return first !== undefined && SHELL_FILE_READING_COMMANDS.has(first);
}

function toolCallKind(item: ToolCallLike): ActionAggregateKind {
  return getActionDisplay(item.step.name, item.step.args).aggregateKind;
}

function toolCallEvidenceKind(item: ToolCallLike): ActionAggregateKind {
  const kind = toolCallKind(item);
  // Shell workarounds that just read file content (cat, sed, grep, …) should
  // be folded into the surrounding file-read evidence cluster so the transcript
  // shows concrete artifacts instead of raw commands.
  if (kind === "command" && isFileReadingShellCommand(item)) {
    return "file_read";
  }
  return kind;
}

function samePhase(a: ToolCallLike, b: ToolCallLike): boolean {
  if (a.step.phaseId === undefined && b.step.phaseId === undefined) return true;
  return a.step.phaseId === b.step.phaseId;
}

export function aggregateSimilarToolCalls(
  items: readonly TimelineItemLike[],
  options: ActivityAggregationOptions = {},
): MaybeAggregatedTimelineItem[] {
  const result: MaybeAggregatedTimelineItem[] = [];
  let currentGroup: ToolCallLike[] = [];
  let currentKind: ActionAggregateKind | null = null;
  let currentPhaseId: string | undefined;

  const flush = () => {
    if (currentGroup.length === 0) return;
    if (currentGroup.length === 1) {
      result.push(currentGroup[0]!);
    } else {
      const first = currentGroup[0];
      if (first) {
        result.push({
          type: "aggregatedToolGroup",
          id: `agg-${first.id ?? first.step.id ?? "tool"}-${currentGroup.length}`,
          aggregateKind: currentKind ?? "other",
          count: currentGroup.length,
          phaseId: currentPhaseId,
          items: [...currentGroup],
          role: first.role,
          inferred: first.inferred,
        });
      }
    }
    currentGroup = [];
    currentKind = null;
    currentPhaseId = undefined;
  };

  for (const item of items) {
    if (item.type !== "toolCall") {
      flush();
      result.push(item);
      continue;
    }
    const toolItem = item as ToolCallLike;
    const kind = toolCallEvidenceKind(toolItem);
    if (
      currentGroup.length > 0 &&
      (currentKind === kind || options.groupMixedKinds) &&
      currentGroup[0] &&
      (options.groupAcrossPhases || samePhase(currentGroup[0]!, toolItem))
    ) {
      currentGroup.push(toolItem);
      if (currentKind !== kind) currentKind = "other";
    } else {
      flush();
      currentGroup = [toolItem];
      currentKind = kind;
      currentPhaseId = toolItem.step.phaseId;
    }
  }
  flush();
  return result;
}

export function isAggregatedToolGroup(
  item: MaybeAggregatedTimelineItem,
): item is AggregatedToolGroupItem {
  return item.type === "aggregatedToolGroup";
}
