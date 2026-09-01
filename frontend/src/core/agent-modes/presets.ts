import type {
  AgentModeName,
  AuditIntensity,
} from "@/components/workspace/mode-selector";

export type ModePresetId =
  | "develop"
  | "audit"
  | "uxui";

export type SkillPackProfile = "develop" | "audit" | "uxui";
export type VerificationPolicy = "light" | "standard" | "strict" | "visual";

export interface ModeOrchestrationPreset {
  id: ModePresetId;
  agentMode: AgentModeName;
  workflowPreset:
    | "develop.iterate"
    | "audit.review"
    | "audit.deep"
    | "uxui.regression";
  skillPackProfile: SkillPackProfile;
  verificationPolicy: VerificationPolicy;
  defaultSkillPacks: string[];
  defaultPlugins: string[];
  promptContract: string;
}

const MODE_PRESETS: Record<ModePresetId, ModeOrchestrationPreset> = {
  develop: {
    id: "develop",
    agentMode: "develop",
    workflowPreset: "develop.iterate",
    skillPackProfile: "develop",
    verificationPolicy: "standard",
    defaultSkillPacks: ["code", "files", "browser"],
    defaultPlugins: ["git", "terminal"],
    promptContract:
      "小步实现、就近测试、保留现有风格；涉及接口/迁移先给设计与兼容、回滚方案再动手，避免无谓大重写；每轮交付说明修改面、验证命令和残余风险。",
  },
  audit: {
    id: "audit",
    agentMode: "audit",
    workflowPreset: "audit.review",
    skillPackProfile: "audit",
    verificationPolicy: "strict",
    defaultSkillPacks: ["code", "files", "review", "tests"],
    defaultPlugins: ["git", "terminal"],
    promptContract:
      "默认先审计不改动；输出发现、证据、严重度、影响面和修复顺序，用户要求修复后再动手。",
  },
  uxui: {
    id: "uxui",
    agentMode: "uxui",
    workflowPreset: "uxui.regression",
    skillPackProfile: "uxui",
    verificationPolicy: "visual",
    defaultSkillPacks: ["browser", "visual", "code", "files"],
    defaultPlugins: ["browser", "terminal"],
    promptContract:
      "优先真实预览和交互走查；关注遮挡、跳变、密度、层级、文案、响应式和视觉质感，修改后必须做浏览器回归。",
  },
};

export function modePresetForAgentMode(
  agentMode: AgentModeName,
): ModeOrchestrationPreset {
  if (agentMode === "audit") return MODE_PRESETS.audit;
  if (agentMode === "uxui") return MODE_PRESETS.uxui;
  return MODE_PRESETS.develop;
}

/**
 * Resolve the workflow preset to SEND, applying the audit-intensity toggle.
 *
 * Only audit mode carries an intensity switch: "max" upgrades the sent preset to
 * `audit.deep`, a soft exhaustive mode — the backend directs the model to
 * fan out and self-check, but the model chooses its own orchestration (depth
 * still governed by the operator orchestration budget). Every other mode — and
 * audit at "standard" — keeps its base preset unchanged.
 */
export function workflowPresetForMode(
  agentMode: AgentModeName,
  auditIntensity: AuditIntensity = "standard",
): ModeOrchestrationPreset["workflowPreset"] {
  if (agentMode === "audit" && auditIntensity === "max") {
    return "audit.deep";
  }
  return modePresetForAgentMode(agentMode).workflowPreset;
}
