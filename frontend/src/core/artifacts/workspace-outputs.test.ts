import { describe, expect, it } from "vitest";

import {
  isInternalArtifactRef,
  isInternalWorkspaceOutput,
} from "./workspace-outputs";

describe("isInternalWorkspaceOutput", () => {
  it.each([
    "plan.md",
    "notes.md",
    "US10792461B2-full.jsonl",
    "US10792461B2-full.jsonl.lock",
    "output/final/US10792461B2.jsonl",
    "final/report.md",
    "stages/draft.md",
  ])("hides working evidence %s", (path) => {
    expect(isInternalWorkspaceOutput(path)).toBe(true);
  });

  it.each([
    "US10792461B2_权利要求1设计规避分析报告.md",
    "reports/final-report.pdf",
    "export/data.jsonl",
  ])("keeps user-facing deliverable %s", (path) => {
    expect(isInternalWorkspaceOutput(path)).toBe(false);
  });

  it("filters stable workspace artifact references too", () => {
    expect(isInternalArtifactRef("workspace-output:final:plan.md")).toBe(true);
    expect(
      isInternalArtifactRef("workspace-output:output:cache/item-full.jsonl"),
    ).toBe(true);
    expect(isInternalArtifactRef("workspace-output:final:report.md")).toBe(
      false,
    );
  });
});
