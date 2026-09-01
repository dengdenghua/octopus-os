import { describe, expect, it } from "vitest";

import type { SubtaskStatus } from "@/core/tasks/types";

import {
  subtaskProgress,
  subtaskProgressPercent,
  subtaskRunState,
} from "./subtask-status-ui";

describe("subtask status UI mapping", () => {
  it.each<SubtaskStatus>(["failed", "cancelled", "timed_out"])(
    "maps terminal non-success status %s to error instead of waiting",
    (status) => {
      expect(subtaskRunState(status)).toBe("error");
      expect(subtaskProgress({ status, progress: 0.2 })).toBe(1);
    },
  );

  it("keeps pending and active statuses visually distinct", () => {
    expect(subtaskRunState("pending")).toBe("waiting");
    expect(subtaskProgress({ status: "pending", progress: 0 })).toBe(0.08);

    expect(subtaskRunState("in_progress")).toBe("running");
    expect(subtaskProgress({ status: "in_progress", progress: 0.5 })).toBe(0.5);
  });

  it("only reports a numeric percent for active tasks with real progress", () => {
    expect(
      subtaskProgressPercent({ status: "in_progress", progress: 0.37 }),
    ).toBe(37);
    // No live progress source → no fabricated constant percentage.
    expect(subtaskProgressPercent({ status: "in_progress", progress: 0 })).toBe(
      null,
    );
    expect(subtaskProgressPercent({ status: "in_progress", progress: 1 })).toBe(
      null,
    );
    // Terminal and pending states render status text, not a number.
    expect(subtaskProgressPercent({ status: "completed", progress: 1 })).toBe(
      null,
    );
    expect(subtaskProgressPercent({ status: "failed", progress: 0.6 })).toBe(
      null,
    );
    expect(subtaskProgressPercent({ status: "pending", progress: 0 })).toBe(
      null,
    );
  });
});
