import { describe, expect, it } from "vitest";

import {
  groupTaskStrategyAfterSubmit,
  groupTaskStrategyContext,
} from "./group-task-strategy-context";

describe("groupTaskStrategyContext", () => {
  it("keeps auto free of a personal or project work contract", () => {
    expect(groupTaskStrategyContext("auto")).toEqual({
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
    });
  });

  it.each(["build", "research"] as const)(
    "maps %s to the matching personal-space contract",
    (strategy) => {
      expect(groupTaskStrategyContext(strategy)).toMatchObject({
        personal_mode: strategy,
        agent_mode: undefined,
        workflow_preset: undefined,
        mode_contract: undefined,
      });
    },
  );

  it.each([
    ["develop", "develop.iterate", "standard", false],
    ["audit", "audit.review", "strict", false],
    ["uxui", "uxui.regression", "visual", true],
  ] as const)(
    "maps %s through the shared project preset",
    (strategy, workflowPreset, verificationPolicy, browserRegression) => {
      const context = groupTaskStrategyContext(strategy);

      expect(context).toMatchObject({
        personal_mode: undefined,
        agent_mode: strategy,
        mode_preset: strategy,
        workflow_preset: workflowPreset,
        skill_pack_profile: strategy,
        verification_policy: verificationPolicy,
      });
      expect(context.default_skill_packs?.length).toBeGreaterThan(0);
      expect(context.default_plugins?.length).toBeGreaterThan(0);
      expect(context.mode_contract).toBeTruthy();
      expect(context.browser_regression_enabled).toBe(
        browserRegression ? true : undefined,
      );
    },
  );

  it("returns to auto after a task turn to avoid accidental heavy follow-ups", () => {
    expect(groupTaskStrategyAfterSubmit()).toBe("auto");
  });
});
