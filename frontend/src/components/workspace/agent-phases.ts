import type { LiveToolEvent } from "./live-tool-timeline";
import { toWorkBlocks, type WorkBlock } from "./work-blocks";
import {
  taskPlanItemActiveLabel,
  taskPlanItemContent,
  taskPlanItemId,
} from "@/core/todos/task-plan";

export type AgentPhaseStatus =
  | "pending"
  | "running"
  | "waiting_approval"
  | "done"
  | "error";

export type AgentPhaseTitleKey =
  | "genericPrepare"
  | "genericExecute"
  | "genericDeliver";

export interface AgentPhase {
  id: string;
  title: string;
  /** When set, the UI renders the localized label for this key instead of
   * the raw ``title`` (which stays available as the accessible/tooltip text). */
  titleKey?: AgentPhaseTitleKey;
  /** Coarse business phase (planning/exploring/…). Sourced from the backend
   * ``phase_kind`` when available, otherwise mapped locally from the title.
   * The UI renders the localized label for this key when ``titleKey`` is
   * absent; the raw ``title`` stays available as tooltip text. */
  businessKey?: BusinessAgentPhaseKey;
  detail?: string;
  status: AgentPhaseStatus;
  blockIds: string[];
}

type DeriveAgentPhasesOptions = {
  hasAnswer?: boolean;
  runSettled?: boolean;
  runFailed?: boolean;
  paused?: boolean;
};

export function deriveAgentPhases(
  events: LiveToolEvent[],
  options: DeriveAgentPhasesOptions = {},
): {
  blocks: WorkBlock[];
  phases: AgentPhase[];
  currentPhase: AgentPhase | null;
} {
  const blocks = toWorkBlocks(events);
  const phases =
    extractTodoPhases(events, blocks, options) ?? buildGenericPhases(blocks);
  const normalizedPhases = normalizePhaseOrdering(phases);
  const currentPhase = pickCurrentPhase(normalizedPhases);
  return { blocks, phases: normalizedPhases, currentPhase };
}

export function progressForPhases(phases: AgentPhase[], current: AgentPhase) {
  const selectedIndex = Math.max(
    0,
    phases.findIndex((phase) => phase.id === current.id),
  );
  const terminal = phases.filter(
    (phase) => phase.status === "done" || phase.status === "error",
  ).length;
  const currentIndex = Math.max(
    1,
    Math.min(phases.length, Math.max(terminal, selectedIndex + 1)),
  );
  return { current: currentIndex, total: phases.length };
}

export function phaseStatusText(status: AgentPhaseStatus): string {
  if (status === "running") return "进行中";
  if (status === "waiting_approval") return "等待确认";
  if (status === "error") return "异常";
  if (status === "done") return "已完成";
  return "待开始";
}

function pickCurrentPhase(phases: AgentPhase[]) {
  return (
    phases.find((phase) => phase.status === "waiting_approval") ??
    phases.find((phase) => phase.status === "running") ??
    phases.find((phase) => phase.status === "error") ??
    phases.find((phase) => phase.status === "pending") ??
    phases[phases.length - 1] ??
    null
  );
}

function extractTodoPhases(
  events: LiveToolEvent[],
  blocks: WorkBlock[],
  options: DeriveAgentPhasesOptions,
): AgentPhase[] | null {
  const newestFirst = [...events].reverse();
  // A real todo_write/first-class todo item owns checklist truth.  The
  // server-phases event is a compatibility projection for old workbench
  // clients and can lag one update behind the source checklist; preferring it
  // here made the inline plan and right workbench disagree after reconnects.
  const todo =
    newestFirst.find(
      (event) =>
        event.name === "todo_write" && event.input?.source !== "turn.phases",
    ) ?? newestFirst.find((event) => event.name === "todo_write");
  const raw = todo?.input?.items ?? todo?.input?.todos;
  if (!Array.isArray(raw) || raw.length < 2) return null;

  const occurrences = new Map<string, number>();
  const phases = raw
    .map((item): AgentPhase | null => {
      if (!item || typeof item !== "object") return null;
      const record = item as Record<string, unknown>;
      const content = taskPlanItemContent(record);
      if (!content) return null;
      const occurrence = occurrences.get(content) ?? 0;
      occurrences.set(content, occurrence + 1);
      const status = normalizePhaseStatus(
        todoStatus(record.status),
        [],
        options,
      );
      const activeLabel = taskPlanItemActiveLabel(record);
      const displayTitle =
        status === "running" && activeLabel ? activeLabel : content;
      const businessKey =
        normalizeBusinessPhaseKey(record.phaseKind ?? record.phase_kind) ??
        businessAgentPhaseKey(displayTitle) ??
        undefined;
      return {
        id: `todo-phase:${taskPlanItemId(record, occurrence)}`,
        title: phaseTitle(displayTitle),
        businessKey,
        status,
        blockIds:
          status === "running" || status === "waiting_approval"
            ? blocks
                .filter(
                  (block) =>
                    block.status === "running" ||
                    block.status === "waiting_approval",
                )
                .map((block) => block.id)
            : [],
      };
    })
    .filter((phase): phase is AgentPhase => Boolean(phase));
  return markFailedTodoPhase(phases, options);
}

function buildGenericPhases(blocks: WorkBlock[]): AgentPhase[] {
  if (blocks.length === 0) return [];
  const buckets: Array<{
    id: string;
    title: string;
    titleKey: AgentPhaseTitleKey;
    blocks: WorkBlock[];
  }> = [
    {
      id: "generic:prepare",
      title: "Gather context",
      titleKey: "genericPrepare",
      blocks: blocks.filter((block) =>
        /read|recall|skill|agent/i.test(block.event.name),
      ),
    },
    {
      id: "generic:execute",
      title: "Work through leads",
      titleKey: "genericExecute",
      blocks: blocks.filter((block) =>
        /browser|search|web|fetch|terminal|shell|file/i.test(block.kind),
      ),
    },
    {
      id: "generic:deliver",
      title: "Pull the answer together",
      titleKey: "genericDeliver",
      blocks: blocks.filter((block) =>
        /todo|write|report|artifact|verification/i.test(
          `${block.event.name} ${block.kind} ${block.actionKey} ${block.title.key}`,
        ),
      ),
    },
  ];
  return buckets
    .filter((bucket) => bucket.blocks.length > 0)
    .map((bucket) => ({
      id: bucket.id,
      title: bucket.title,
      titleKey: bucket.titleKey,
      status: statusFromBlockList(bucket.blocks),
      blockIds: bucket.blocks.map((block) => block.id),
    }));
}

function statusFromBlockList(blocks: WorkBlock[]): AgentPhaseStatus {
  // A settled turn is not completion evidence for a blocked/approval step.
  // Keep the waiting state visible until an explicit receipt (or a later
  // todo/phase update) marks it done. Otherwise an interrupted run paints the
  // last spinner green merely because the assistant emitted a final sentence.
  if (blocks.some((block) => block.status === "waiting_approval")) {
    return "waiting_approval";
  }
  if (blocks.some((block) => block.status === "running")) {
    return "running";
  }
  if (blocks.some((block) => block.status === "error")) return "error";
  return "done";
}

function normalizePhaseStatus(
  status: AgentPhaseStatus,
  blocks: WorkBlock[],
  options: DeriveAgentPhasesOptions,
): AgentPhaseStatus {
  if (options.paused) return status;
  if (!options.runSettled) return status;
  if (
    options.runFailed &&
    (status === "running" || status === "waiting_approval")
  )
    return "error";
  if (options.runFailed && status === "pending") return "pending";
  // A final answer is prose, not completion evidence. Preserve explicit todo
  // truth on a successful terminal turn: only a todo update may turn pending
  // or running into done. This also keeps interrupted/partial deliveries from
  // painting the remaining checklist green merely because some answer text
  // exists.
  if (!options.runFailed && options.hasAnswer) return status;
  if (status === "pending") return "pending";
  if (status === "waiting_approval") return status;
  if (status !== "running") return status;
  return blocks.length === 0 ||
    blocks.every((block) => block.status === "waiting_approval")
    ? "error"
    : status;
}

function markFailedTodoPhase(
  phases: AgentPhase[],
  options: DeriveAgentPhasesOptions,
): AgentPhase[] {
  if (
    options.paused ||
    !options.runSettled ||
    (options.hasAnswer && !options.runFailed)
  ) {
    return phases;
  }
  let marked = false;
  return phases.map((phase) => {
    if (phase.status === "done") return phase;
    if (!marked) {
      marked = true;
      return { ...phase, status: "error" };
    }
    return phase.status === "running" || phase.status === "waiting_approval"
      ? { ...phase, status: "pending" }
      : phase;
  });
}

function todoStatus(value: unknown): AgentPhaseStatus {
  if (value === "completed" || value === "done") return "done";
  if (value === "in_progress" || value === "running") return "running";
  if (value === "waiting_approval" || value === "awaiting_approval")
    return "waiting_approval";
  if (value === "error" || value === "failed") return "error";
  return "pending";
}

function phaseTitle(title: string) {
  const displayTitle = normalizeAgentPhaseTitle(title);
  return displayTitle || `进行中`;
}

export type BusinessAgentPhaseKey =
  | "planning"
  | "exploring"
  | "implementing"
  | "testing"
  | "deploying";

/**
 * Map a free-form phase title (usually a todo item written by the model) to a
 * coarse business phase so the workbench outline can show a readable label
 * ("Analyzing requirements") instead of the raw technical wording. Returns
 * null when nothing matches — the raw title is shown unchanged.
 */
export function businessAgentPhaseKey(
  title: string,
): BusinessAgentPhaseKey | null {
  const text = title.toLowerCase();
  if (/deploy|release|publish|ship|部署|上线|发布/.test(text)) {
    return "deploying";
  }
  if (
    /test|verify|validat|check|qa|lint|build|测试|验证|确认|检查|构建|打包/.test(
      text,
    )
  ) {
    return "testing";
  }
  if (
    /implement|edit|fix|code|refactor|write|add|update|modify|change|create|patch|实现|修改|修复|改|新增|添加|更新|重构|接入|迁移|搭建/.test(
      text,
    )
  ) {
    return "implementing";
  }
  if (/plan|design|scope|spec|todo|规划|计划|设计|方案/.test(text)) {
    return "planning";
  }
  if (
    /explore|read|inspect|analy[sz]e|investigat|research|scan|review|understand|study|浏览|阅读|了解|分析|调研|研究|排查|查看|梳理/.test(
      text,
    )
  ) {
    return "exploring";
  }
  return null;
}

/**
 * Validate a backend-provided ``phase_kind`` value. Returns null for
 * "other", unknown strings, or missing values so the caller can fall back
 * to the local ``businessAgentPhaseKey`` mapping.
 */
export function normalizeBusinessPhaseKey(
  value: unknown,
): BusinessAgentPhaseKey | null {
  if (
    value === "planning" ||
    value === "exploring" ||
    value === "implementing" ||
    value === "testing" ||
    value === "deploying"
  ) {
    return value;
  }
  return null;
}

/**
 * Resolve the text the UI should show for a phase.
 *
 * Synthetic phases use localized generic labels. Real todo phases keep the
 * model-authored title so the plan stays specific to the current task instead
 * of collapsing into repeated labels such as "Implement" or "Verify".
 */
export function agentPhaseDisplayTitle(
  phase: AgentPhase,
  labels: Record<AgentPhaseTitleKey | BusinessAgentPhaseKey, string>,
): string {
  if (phase.titleKey) return labels[phase.titleKey];
  return phase.title;
}

export function normalizeAgentPhaseTitle(title: string) {
  const clean = title.replace(/\s+/g, " ").trim();
  const withoutMachinePrefix = clean
    .replace(
      /^(?:phase|阶段|step|步骤)\s*[\d一二三四五六七八九十]+(?:\.\d+)?\s*[:：.)、-]?\s*/i,
      "",
    )
    .trim();
  return withoutMachinePrefix || clean;
}

function normalizePhaseOrdering(phases: AgentPhase[]): AgentPhase[] {
  const currentIndex = phases.findIndex(
    (phase) =>
      phase.status === "running" || phase.status === "waiting_approval",
  );
  if (currentIndex < 0) return phases;
  return phases.map((phase, index) =>
    index > currentIndex &&
    (phase.status === "running" || phase.status === "waiting_approval")
      ? { ...phase, status: "pending" }
      : phase,
  );
}
