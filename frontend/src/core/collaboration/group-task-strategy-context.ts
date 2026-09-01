import type { AgentModeName } from "@/components/workspace/mode-selector";
import type { PersonalMode } from "@/components/workspace/personal-mode-selector";
import type { GroupTaskStrategy } from "@/components/workspace/group-task-strategy";
import {
  modePresetForAgentMode,
  workflowPresetForMode,
  type VerificationPolicy,
} from "@/core/agent-modes/presets";

/**
 * Work-contract fields owned by the group task strategy picker.
 *
 * Every key is present deliberately: spreading this object over the ordinary
 * personal/project context clears any hidden stale selector state for group
 * turns. The realtime transport removes `undefined` values before sending.
 */
export type GroupTaskStrategyContext = {
  personal_mode: PersonalMode | undefined;
  personal_instructions: undefined;
  agent_mode: AgentModeName | undefined;
  mode_preset: string | undefined;
  workflow_preset: string | undefined;
  browser_regression_enabled: boolean | undefined;
  skill_pack_profile: string | undefined;
  verification_policy: VerificationPolicy | undefined;
  default_skill_packs: string[] | undefined;
  default_plugins: string[] | undefined;
  mode_contract: string | undefined;
};

const EMPTY_GROUP_TASK_CONTEXT: GroupTaskStrategyContext = {
  personal_mode: undefined,
  personal_instructions: undefined,
  agent_mode: undefined,
  mode_preset: undefined,
  workflow_preset: undefined,
  browser_regression_enabled: undefined,
  skill_pack_profile: undefined,
  verification_policy: undefined,
  default_skill_packs: undefined,
  default_plugins: undefined,
  mode_contract: undefined,
};

export function groupTaskStrategyContext(
  strategy: GroupTaskStrategy,
): GroupTaskStrategyContext {
  if (strategy === "auto") return { ...EMPTY_GROUP_TASK_CONTEXT };
  if (strategy === "build" || strategy === "research") {
    return {
      ...EMPTY_GROUP_TASK_CONTEXT,
      personal_mode: strategy,
    };
  }

  const agentMode: AgentModeName = strategy;
  const preset = modePresetForAgentMode(agentMode);
  return {
    ...EMPTY_GROUP_TASK_CONTEXT,
    agent_mode: agentMode,
    mode_preset: preset.id,
    workflow_preset: workflowPresetForMode(agentMode),
    browser_regression_enabled: agentMode === "uxui" ? true : undefined,
    skill_pack_profile: preset.skillPackProfile,
    verification_policy: preset.verificationPolicy,
    default_skill_packs: preset.defaultSkillPacks,
    default_plugins: preset.defaultPlugins,
    mode_contract: preset.promptContract,
  };
}

/** Group task strategies are one-turn intents, not sticky room settings. */
export function groupTaskStrategyAfterSubmit(): GroupTaskStrategy {
  return "auto";
}
