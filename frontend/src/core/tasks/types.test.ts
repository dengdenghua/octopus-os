import { describe, expect, test } from "vitest";

import {
  isSubtaskActive,
  isSubtaskTerminal,
  SUBTASK_STATUS_LABELS,
} from "./types";

describe("isSubtaskActive", () => {
  test("returns true for active statuses", () => {
    expect(isSubtaskActive("reasoning")).toBe(true);
    expect(isSubtaskActive("iterating")).toBe(true);
    expect(isSubtaskActive("generating")).toBe(true);
    expect(isSubtaskActive("analyzing")).toBe(true);
    expect(isSubtaskActive("summarizing")).toBe(true);
    expect(isSubtaskActive("in_progress")).toBe(true);
  });

  test("returns false for terminal statuses", () => {
    expect(isSubtaskActive("completed")).toBe(false);
    expect(isSubtaskActive("failed")).toBe(false);
  });

  test("returns false for pending", () => {
    expect(isSubtaskActive("pending")).toBe(false);
  });
});

describe("isSubtaskTerminal", () => {
  test("returns true for completed and failed", () => {
    expect(isSubtaskTerminal("completed")).toBe(true);
    expect(isSubtaskTerminal("failed")).toBe(true);
  });

  test("returns false for all other statuses", () => {
    expect(isSubtaskTerminal("pending")).toBe(false);
    expect(isSubtaskTerminal("reasoning")).toBe(false);
    expect(isSubtaskTerminal("in_progress")).toBe(false);
  });
});

describe("SUBTASK_STATUS_LABELS", () => {
  test("has a label for every status", () => {
    const statuses: string[] = [
      "pending",
      "reasoning",
      "iterating",
      "generating",
      "analyzing",
      "summarizing",
      "in_progress",
      "completed",
      "failed",
    ];
    for (const s of statuses) {
      expect(
        s in SUBTASK_STATUS_LABELS,
        `Missing label for status: ${s}`,
      ).toBeTruthy();
    }
  });
});
