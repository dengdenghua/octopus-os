import { beforeEach, describe, expect, it, vi } from "vitest";

const actorState = vi.hoisted(() => ({ value: "anonymous" }));

vi.mock("@/core/auth/api", () => ({
  currentActorId: () => actorState.value,
}));

import {
  appendStreamTelemetry,
  readStreamTelemetry,
  streamTelemetryStorageKey,
} from "./stream-telemetry";

function record(id: string) {
  return {
    id,
    threadId: `${id}-thread`,
    turnId: `${id}-turn`,
    startedAt: 1,
    completedAt: 2,
    durationMs: 1,
    ttftMs: 1,
    maxDeltaGapMs: 1,
    stalledAtEnd: false,
    outcome: "completed" as const,
  };
}

describe("stream telemetry actor storage", () => {
  beforeEach(() => {
    localStorage.clear();
    actorState.value = "anonymous";
  });

  it("keeps diagnostic records separate between actors", () => {
    actorState.value = "alice";
    appendStreamTelemetry(record("alice"));
    actorState.value = "bob";
    appendStreamTelemetry(record("bob"));

    expect(readStreamTelemetry()).toEqual([record("bob")]);
    actorState.value = "alice";
    expect(readStreamTelemetry()).toEqual([record("alice")]);
    expect(localStorage.getItem(streamTelemetryStorageKey("bob"))).toContain(
      '"id":"bob"',
    );
  });
});
