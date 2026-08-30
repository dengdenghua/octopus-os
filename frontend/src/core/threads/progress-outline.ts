/**
 * 「进展」面板叙事大纲 selector（纯函数模块，无 React 依赖）。
 *
 * 由带语义角色（role）与轮次（iteration）的时间线步骤派生「轮次大纲」：
 * 每轮聚合 意图摘要（一行）、执行计数（如「3 个动作」）、已确认事实列表
 * （各一行），供侧边栏「进展」面板按 iteration 分组渲染，替代 step 平铺。
 *
 * 输入声明为最小结构形状（与 components/workspace/messages/message-group.tsx
 * 的 CoTStep 结构兼容），使本 core 模块不依赖 components 层
 * （与 core/threads/sidebar.ts 的层级约定一致）。
 */

/**
 * 时间线语义角色。与 components/workspace/messages/timeline-role.ts 的
 * ``TimelineRole`` 对齐；此处以字面量联合重新声明以避免跨层依赖。
 */
export type ProgressOutlineRole = "intent" | "execution" | "fact" | "answer";

/** 可参与大纲构建的步骤最小形状（结构化类型兼容 CoTStep）。 */
export interface ProgressOutlineStep {
  type: string;
  iteration?: number;
  role?: ProgressOutlineRole;
  /** reasoning 步骤文本 */
  reasoning?: string | null;
  /** commentary 步骤文本 */
  commentary?: string;
}

export interface OutlineRound {
  iteration: number;
  /** 本轮意图摘要（单行，已截断）；本轮无意图条目时为 null（不编造）。 */
  intentText: string | null;
  /** 本轮执行条目计数（toolCall / actionCallback）。 */
  executionCount: number;
  /** 本轮已确认事实列表（每条单行，已截断）。 */
  facts: string[];
}

/** 大纲单行最大长度：超出部分截断，保证「一行」的渲染约束。 */
const MAX_LINE_LENGTH = 120;

function isExecutionStepType(type: string): boolean {
  return type === "toolCall" || type === "actionCallback";
}

/** 提取步骤叙事文本（仅 intent / fact 类步骤有文本）。 */
function narrativeTextOf(step: ProgressOutlineStep): string | null {
  if (typeof step.reasoning === "string" && step.reasoning.trim()) {
    return step.reasoning;
  }
  if (typeof step.commentary === "string" && step.commentary.trim()) {
    return step.commentary;
  }
  return null;
}

/** 单行化并截断：折叠所有空白（含换行）为一空格，超限以 … 结尾。 */
function truncateLine(text: string, maxLength = MAX_LINE_LENGTH): string {
  const line = text.replace(/\s+/g, " ").trim();
  if (line.length <= maxLength) return line;
  return `${line.slice(0, maxLength - 1).trimEnd()}…`;
}

/**
 * 单趟遍历把时间线步骤聚合为按 iteration 升序排列的轮次大纲。
 *
 * 角色判定：优先步骤自带 role；缺失时按类型与位置 fallback
 * （执行类 → execution；首个执行之前的叙事 → intent，之后 → fact），
 * 与 timeline-role.ts 的位置推断口径一致。
 */
export function buildProgressOutline(
  steps: readonly ProgressOutlineStep[],
): OutlineRound[] {
  const rounds = new Map<number, OutlineRound>();
  let hasExecutionBefore = false;

  for (const step of steps) {
    const iteration = step.iteration ?? 1;
    let round = rounds.get(iteration);
    if (!round) {
      round = { iteration, intentText: null, executionCount: 0, facts: [] };
      rounds.set(iteration, round);
    }

    if (isExecutionStepType(step.type)) {
      hasExecutionBefore = true;
      round.executionCount += 1;
      continue;
    }

    const text = narrativeTextOf(step);
    if (!text) continue;
    const role = step.role ?? (hasExecutionBefore ? "fact" : "intent");
    if (role === "fact") {
      round.facts.push(truncateLine(text));
    } else if (role === "intent" && round.intentText === null) {
      // 每轮只保留首个意图条目作为摘要（一行）。
      round.intentText = truncateLine(text);
    }
  }

  return [...rounds.values()].sort((a, b) => a.iteration - b.iteration);
}
