/**
 * 时间线语义角色层（纯函数模块，无 React 依赖）。
 *
 * 为对话区时间线的每个可见条目赋予显式语义角色：
 * - intent：简短意图（这轮要做什么）
 * - execution：实际执行（工具调用 / 动作回调）
 * - fact：已确认事实（工具结果之后的过程确认）
 * - answer：最终回答（消息正文，不在步骤层判定，见 isAnswerContent）
 *
 * 推断优先级：后端结构化协议字段 > 消息类型与位置（fallback，标记 inferred=true）。
 * 角色只是附加信息，渲染层暂不消费，不改变既有排序 / 分组 / 去重行为。
 */
import type { Message } from "@/core/api/types";

import { stripTraceLabelPrefixes } from "./trace-labels";

export type TimelineRole = "intent" | "execution" | "fact" | "answer";

export interface RoleInference {
  role: TimelineRole;
  /** true 表示该角色来自 fallback 推断（无结构化协议字段）。 */
  inferred: boolean;
}

/**
 * 可参与角色推断的步骤最小形状。
 * 与 message-group.tsx 的 CoTStep / TimelineItem 成员结构对齐（结构化类型兼容）。
 */
export interface RoleAssignableStep {
  id?: string;
  messageId?: string;
  type: string;
  phaseId?: string;
  progressSequence?: number;
  timelineSequence?: number;
  /** reasoning 步骤文本 */
  reasoning?: string | null;
  /** commentary 步骤文本 */
  commentary?: string;
  /** actionCallback 步骤文本 */
  actionText?: string;
  /** 已填充的角色（propagate 到分组条目时使用） */
  role?: TimelineRole;
  inferred?: boolean;
}

export interface TimelineRoleContext {
  /** 当前步骤之前是否已出现执行类步骤（toolCall / actionCallback）。 */
  hasExecutionBefore: boolean;
  /** 来源消息带 additional_kwargs.public_progress === true 的消息 id 集合。 */
  publicProgressMessageIds?: ReadonlySet<string>;
  /**
   * 已完成角色标注的叙事文本 fingerprint → 角色。
   * 与 convertToSteps 内 timelineNarrativeFingerprint 去重逻辑兼容：
   * 同一段文本即使因位置变化也不会同时被标成 intent 与 fact。
   */
  seenNarrativeRoles?: ReadonlyMap<string, TimelineRole>;
}

/** 与 message-group.tsx 的 timelineNarrativeFingerprint 保持同一归一化口径。 */
export function narrativeFingerprint(value: string): string {
  return stripTraceLabelPrefixes(value)
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function isExecutionStepType(type: string): boolean {
  return type === "toolCall" || type === "actionCallback";
}

/** 提取步骤叙事文本（仅 intent / fact 类步骤有文本）。 */
function narrativeTextOf(step: RoleAssignableStep): string | null {
  if (typeof step.reasoning === "string" && step.reasoning.trim()) {
    return step.reasoning;
  }
  if (typeof step.commentary === "string" && step.commentary.trim()) {
    return step.commentary;
  }
  return null;
}

/** 步骤是否带结构化协议字段（或来源消息被标记为 public_progress）。 */
function hasStructuredProtocol(
  step: RoleAssignableStep,
  context: TimelineRoleContext,
): boolean {
  if (step.phaseId !== undefined) return true;
  if (step.progressSequence !== undefined) return true;
  if (step.timelineSequence !== undefined) return true;
  if (
    step.messageId !== undefined &&
    context.publicProgressMessageIds?.has(step.messageId)
  ) {
    return true;
  }
  return false;
}

/**
 * 推断单个步骤的语义角色（纯函数）。
 *
 * 结构化协议字段齐全时角色来自协议判定（inferred=false）；
 * 否则按类型与位置 fallback（inferred=true）。
 * answer 角色不在步骤层判定。
 */
export function roleForStep(
  step: RoleAssignableStep,
  context: TimelineRoleContext,
): RoleInference {
  const structured = hasStructuredProtocol(step, context);

  // 执行类：协议与 fallback 结论一致，均为 execution。
  if (isExecutionStepType(step.type)) {
    return { role: "execution", inferred: !structured };
  }

  // 叙事类（reasoning / commentary / reasoningGroup）：按位置在 intent 与 fact 间判定。
  // 首个 commentary / 工具调用之前的 reasoning → intent；工具调用之后 → fact。
  const positionalRole: TimelineRole = context.hasExecutionBefore
    ? "fact"
    : "intent";

  // 去重兼容：同一 fingerprint 文本已被标注过角色时，沿用首次角色，
  // 避免同一段旁白一处标 intent、另一处标 fact。
  const text = narrativeTextOf(step);
  if (text) {
    const fingerprint = narrativeFingerprint(text);
    const seenRole = fingerprint
      ? context.seenNarrativeRoles?.get(fingerprint)
      : undefined;
    if (seenRole) {
      return { role: seenRole, inferred: !structured };
    }
  }

  return { role: positionalRole, inferred: !structured };
}

export interface AssignTimelineRolesOptions {
  /** 来源消息带 public_progress 协议标记的消息 id 集合。 */
  publicProgressMessageIds?: ReadonlySet<string>;
}

/**
 * 单趟遍历为整条步骤序列填充 role / inferred（返回新数组，不改入参）。
 * 步骤顺序、内容完全保持不变。
 */
export function assignTimelineRoles<T extends RoleAssignableStep>(
  steps: readonly T[],
  options: AssignTimelineRolesOptions = {},
): T[] {
  const seenNarrativeRoles = new Map<string, TimelineRole>();
  let hasExecutionBefore = false;

  return steps.map((step) => {
    const inference = roleForStep(step, {
      hasExecutionBefore,
      publicProgressMessageIds: options.publicProgressMessageIds,
      seenNarrativeRoles,
    });
    if (isExecutionStepType(step.type)) {
      hasExecutionBefore = true;
    } else {
      const text = narrativeTextOf(step);
      if (text) {
        const fingerprint = narrativeFingerprint(text);
        if (fingerprint && !seenNarrativeRoles.has(fingerprint)) {
          seenNarrativeRoles.set(fingerprint, inference.role);
        }
      }
    }
    return { ...step, role: inference.role, inferred: inference.inferred };
  });
}

/** 判断消息是否含可见正文 content（thinking 等非正文部分不算）。 */
function hasVisibleAnswerBody(message: Message): boolean {
  const content = message.content;
  if (typeof content === "string") return content.trim().length > 0;
  if (Array.isArray(content)) {
    return content.some(
      (part) =>
        part?.type === "text" &&
        typeof part.text === "string" &&
        part.text.trim().length > 0,
    );
  }
  return false;
}

/**
 * 判定该 ai 消息是否为当前消息组的最终回答：
 * 它是 messages 中最后一条含可见正文 content 的 ai 消息。
 * （供 Task 6 最终回答视觉分层使用。）
 */
export function isAnswerContent(
  message: Message,
  messages: readonly Message[],
): boolean {
  if (message.type !== "ai") return false;
  if (!hasVisibleAnswerBody(message)) return false;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const candidate = messages[index];
    if (candidate?.type === "ai" && hasVisibleAnswerBody(candidate)) {
      return (
        candidate === message ||
        (message.id !== undefined && candidate.id === message.id)
      );
    }
  }
  return false;
}
