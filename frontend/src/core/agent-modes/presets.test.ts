import { describe, expect, it } from "vitest";

import { modePresetForAgentMode, workflowPresetForMode } from "./presets";

describe("modePresetForAgentMode", () => {
  it("maps top-level work modes to orchestration presets", () => {
    expect(modePresetForAgentMode("develop")).toMatchObject({
      id: "develop",
      workflowPreset: "develop.iterate",
      skillPackProfile: "develop",
      verificationPolicy: "standard",
    });
    expect(modePresetForAgentMode("audit")).toMatchObject({
      id: "audit",
      workflowPreset: "audit.review",
      skillPackProfile: "audit",
      verificationPolicy: "strict",
    });
    expect(modePresetForAgentMode("uxui")).toMatchObject({
      id: "uxui",
      workflowPreset: "uxui.regression",
      skillPackProfile: "uxui",
      verificationPolicy: "visual",
    });
  });

  it("keeps audit as the only user-facing audit mode", () => {
    expect(modePresetForAgentMode("audit")).toMatchObject({
      id: "audit",
      agentMode: "audit",
      workflowPreset: "audit.review",
      skillPackProfile: "audit",
      verificationPolicy: "strict",
    });
  });
});

describe("workflowPresetForMode", () => {
  it("upgrades audit to deep only at max intensity", () => {
    expect(workflowPresetForMode("audit", "standard")).toBe("audit.review");
    expect(workflowPresetForMode("audit", "max")).toBe("audit.deep");
    // Default intensity is the conservative single-pass review.
    expect(workflowPresetForMode("audit")).toBe("audit.review");
  });

  it("ignores intensity for non-audit modes (no deep leak)", () => {
    expect(workflowPresetForMode("develop", "max")).toBe("develop.iterate");
    expect(workflowPresetForMode("uxui", "max")).toBe("uxui.regression");
  });
});
