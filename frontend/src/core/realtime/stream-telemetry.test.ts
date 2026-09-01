import { beforeEach, describe, expect, it } from "vitest";

import { emptyVitalsMarks } from "./stream-vitals";
import {
  appendStreamTelemetry,
  clearStreamTelemetry,
  createStreamTurnTelemetry,
  readStreamTelemetry,
  summarizeStreamTelemetry,
  type StreamTurnTelemetry,
} from "./stream-telemetry";

function record(overrides: Partial<StreamTurnTelemetry> = {}) {
  return {
    id: "thread-1:turn-1",
    threadId: "thread-1",
    turnId: "turn-1",
    startedAt: 1_000,
    completedAt: 5_000,
    durationMs: 4_000,
    ttftMs: 500,
    maxDeltaGapMs: 800,
    stalledAtEnd: false,
    outcome: "completed" as const,
    ...overrides,
  };
}

describe("stream telemetry", () => {
  beforeEach(() => localStorage.clear());

  it("creates a privacy-safe turn record from vitals marks", () => {
    const marks = emptyVitalsMarks();
    marks.turnStartedAt = 1_000;
    marks.firstDeltaAt = 1_600;
    marks.lastActivityAt = 2_000;
    marks.maxDeltaGapMs = 350;

    expect(
      createStreamTurnTelemetry({
        threadId: "thread-1",
        turnId: "turn-1",
        outcome: "failed",
        marks,
        completedAt: 13_000,
      }),
    ).toEqual(
      expect.objectContaining({
        durationMs: 12_000,
        ttftMs: 600,
        maxDeltaGapMs: 350,
        stalledAtEnd: true,
        outcome: "failed",
      }),
    );
  });

  it("rejects marks captured for a different turn", () => {
    const marks = emptyVitalsMarks();
    marks.activeTurnId = "turn-old";
    marks.turnStartedAt = 1_000;

    expect(
      createStreamTurnTelemetry({
        threadId: "thread-1",
        turnId: "turn-new",
        outcome: "completed",
        marks,
        completedAt: 2_000,
      }),
    ).toBeNull();
  });

  it("deduplicates turn records and clears them", () => {
    appendStreamTelemetry(record());
    appendStreamTelemetry(record({ durationMs: 5_000 }));

    expect(readStreamTelemetry()).toHaveLength(1);
    expect(readStreamTelemetry()[0]?.durationMs).toBe(5_000);
    clearStreamTelemetry();
    expect(readStreamTelemetry()).toEqual([]);
  });

  it("summarizes percentiles and abnormal outcomes", () => {
    const summary = summarizeStreamTelemetry([
      record({ ttftMs: 100, maxDeltaGapMs: 200 }),
      record({ id: "2", ttftMs: 300, maxDeltaGapMs: 600 }),
      record({
        id: "3",
        ttftMs: null,
        maxDeltaGapMs: 1_000,
        stalledAtEnd: true,
        outcome: "interrupted",
      }),
    ]);

    expect(summary).toEqual({
      count: 3,
      ttftP50Ms: 100,
      ttftP95Ms: 300,
      maxGapP95Ms: 1_000,
      stalledRate: 1 / 3,
      unsuccessfulRate: 1 / 3,
    });
  });
});
