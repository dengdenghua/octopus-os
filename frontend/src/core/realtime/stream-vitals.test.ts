import { describe, expect, it } from "vitest";

import {
  applyVitalNotification,
  classifyVitals,
  DEFAULT_VITALS_THRESHOLDS,
  emptyVitalsMarks,
  formatStreamElapsed,
  seedVitalsFromResumedTurn,
  type ClassifyInput,
  type VitalsMarks,
} from "./stream-vitals";

const T0 = 1_000_000; // arbitrary epoch-ms origin

describe("formatStreamElapsed", () => {
  it("keeps short waits compact and makes minute-long waits readable", () => {
    expect(formatStreamElapsed(0)).toBe("0s");
    expect(formatStreamElapsed(59_900)).toBe("59s");
    expect(formatStreamElapsed(104_500)).toBe("1m 44s");
    expect(formatStreamElapsed(3_905_000)).toBe("1h 05m");
  });
});

function marksAtTurnStart(): VitalsMarks {
  const m = emptyVitalsMarks();
  applyVitalNotification(m, { method: "turn/started" }, T0);
  return m;
}

function classify(
  marks: VitalsMarks,
  now: number,
  overrides: Partial<Omit<ClassifyInput, "marks">> = {},
) {
  return classifyVitals(
    {
      marks,
      connected: true,
      turnActive: true,
      hasRunningWork: false,
      ...overrides,
    },
    now,
  );
}

describe("applyVitalNotification", () => {
  it("turn/started resets marks and stamps the origin", () => {
    const m = emptyVitalsMarks();
    m.firstDeltaAt = 42;
    m.maxDeltaGapMs = 999;
    applyVitalNotification(m, { method: "turn/started" }, T0);
    expect(m.turnStartedAt).toBe(T0);
    expect(m.firstDeltaAt).toBeNull();
    expect(m.firstAgentActivityAt).toBeNull();
    expect(m.maxDeltaGapMs).toBe(0);
    expect(m.lastActivityAt).toBe(T0);
  });

  it("binds marks to the wire turn id", () => {
    const m = emptyVitalsMarks();
    applyVitalNotification(
      m,
      { method: "turn/started", params: { turn: { id: "turn-7" } } },
      T0,
    );
    expect(m.activeTurnId).toBe("turn-7");
  });

  it("first text delta fixes firstDeltaAt (TTFT) and lastDeltaAt", () => {
    const m = marksAtTurnStart();
    applyVitalNotification(m, { method: "item/agentMessage/delta" }, T0 + 800);
    expect(m.firstDeltaAt).toBe(T0 + 800);
    expect(m.lastDeltaAt).toBe(T0 + 800);
    expect(m.firstAgentActivityAt).toBe(T0 + 800);
  });

  it("tracks the worst inter-delta gap (streaming interval)", () => {
    const m = marksAtTurnStart();
    applyVitalNotification(m, { method: "item/agentMessage/delta" }, T0 + 500);
    applyVitalNotification(m, { method: "item/agentMessage/delta" }, T0 + 700); // gap 200
    applyVitalNotification(
      m,
      { method: "item/agentMessage/delta" },
      T0 + 3_000,
    ); // gap 2300
    applyVitalNotification(
      m,
      { method: "item/agentMessage/delta" },
      T0 + 3_100,
    ); // gap 100
    expect(m.maxDeltaGapMs).toBe(2_300);
  });

  it("heartbeat records elapsedS and counts as activity", () => {
    const m = marksAtTurnStart();
    applyVitalNotification(
      m,
      { method: "turn/heartbeat", params: { elapsedS: 12 } },
      T0 + 5_000,
    );
    expect(m.heartbeatElapsedS).toBe(12);
    expect(m.lastHeartbeatAt).toBe(T0 + 5_000);
    expect(m.lastActivityAt).toBe(T0 + 5_000);
    expect(m.firstAgentActivityAt).toBeNull();
  });

  it("reasoning + tool-progress deltas count as activity but not text", () => {
    const m = marksAtTurnStart();
    applyVitalNotification(
      m,
      { method: "item/reasoning/textDelta" },
      T0 + 2_000,
    );
    applyVitalNotification(
      m,
      { method: "item/commandExecution/outputDelta" },
      T0 + 4_000,
    );
    expect(m.lastActivityAt).toBe(T0 + 4_000);
    expect(m.lastDeltaAt).toBeNull(); // no visible text yet
    expect(m.firstDeltaAt).toBeNull();
    expect(m.firstAgentActivityAt).toBe(T0 + 2_000);
  });

  it("does not treat the echoed user message as an agent response", () => {
    const m = marksAtTurnStart();
    applyVitalNotification(
      m,
      {
        method: "item/started",
        params: { item: { type: "userMessage" } },
      },
      T0 + 50,
    );

    expect(m.lastActivityAt).toBe(T0 + 50);
    expect(m.firstAgentActivityAt).toBeNull();
  });
});

describe("seedVitalsFromResumedTurn", () => {
  it("gives a resumed active turn a finite liveness baseline", () => {
    const m = emptyVitalsMarks();
    seedVitalsFromResumedTurn(
      m,
      {
        id: "turn-resumed",
        status: "inProgress",
        startedAt: new Date(T0 - 30_000).toISOString(),
      },
      T0,
    );

    expect(m.activeTurnId).toBe("turn-resumed");
    expect(m.turnStartedAt).toBe(T0 - 30_000);
    expect(m.lastActivityAt).toBe(T0);
    // Before the first agent item arrives, stay in "waiting" indefinitely
    // — the model may be doing server-side reasoning (especially non-thinking
    // models that emit zero intermediate tokens). Flagging TTFT silence as
    // "slow" was a false positive that made every long-pondering turn look stuck.
    expect(classify(m, T0 + 9_999).phase).toBe("waiting");
    expect(classify(m, T0 + 10_000).phase).toBe("waiting");
    expect(classify(m, T0 + 60_000).phase).toBe("waiting");
  });

  it("restores working state when a resumed turn already has agent output", () => {
    const m = emptyVitalsMarks();
    seedVitalsFromResumedTurn(
      m,
      {
        id: "turn-resumed",
        status: "inProgress",
        items: [{ type: "userMessage" }, { type: "reasoning" }],
      },
      T0,
    );

    expect(m.firstAgentActivityAt).toBe(T0);
    expect(classify(m, T0 + 1_000).phase).toBe("working");
  });

  it("preserves same-turn metrics but resets them for a different turn", () => {
    const m = marksAtTurnStart();
    m.activeTurnId = "turn-a";
    applyVitalNotification(m, { method: "item/agentMessage/delta" }, T0 + 500);

    seedVitalsFromResumedTurn(
      m,
      { id: "turn-a", status: "inProgress" },
      T0 + 5_000,
    );
    expect(m.firstDeltaAt).toBe(T0 + 500);

    seedVitalsFromResumedTurn(
      m,
      { id: "turn-b", status: "inProgress" },
      T0 + 6_000,
    );
    expect(m.activeTurnId).toBe("turn-b");
    expect(m.firstDeltaAt).toBeNull();
    expect(m.firstAgentActivityAt).toBeNull();
    expect(m.lastActivityAt).toBe(T0 + 6_000);
  });
});

describe("classifyVitals", () => {
  it("idle when no turn is active", () => {
    const v = classify(marksAtTurnStart(), T0 + 1_000, { turnActive: false });
    expect(v.phase).toBe("idle");
  });

  it("disconnected trumps everything while a turn is active", () => {
    const m = marksAtTurnStart();
    applyVitalNotification(m, { method: "item/agentMessage/delta" }, T0 + 100);
    const v = classify(m, T0 + 200, { connected: false });
    expect(v.phase).toBe("disconnected");
    expect(v.stalled).toBe(true);
  });

  it("streaming while text deltas are fresh", () => {
    const m = marksAtTurnStart();
    applyVitalNotification(m, { method: "item/agentMessage/delta" }, T0 + 900);
    const v = classify(m, T0 + 1_200); // 300ms since delta < 1500
    expect(v.phase).toBe("streaming");
    expect(v.ttftMs).toBe(900);
  });

  it("waiting before the first token while still lively", () => {
    const m = marksAtTurnStart(); // only turn/started so far
    const v = classify(m, T0 + 3_000);
    expect(v.phase).toBe("waiting");
    expect(v.ttftMs).toBeNull();
  });

  it("working when a tool is running even through long silence", () => {
    const m = marksAtTurnStart();
    // No activity for 40s, but a tool item is inProgress.
    const v = classify(m, T0 + 40_000, { hasRunningWork: true });
    expect(v.phase).toBe("working");
    expect(v.stalled).toBe(false);
  });

  it("working during a between-chunks pause after text has started", () => {
    const m = marksAtTurnStart();
    applyVitalNotification(m, { method: "item/agentMessage/delta" }, T0 + 500);
    // 4s since last delta: past streaming-fresh, but within activity window.
    const v = classify(m, T0 + 4_500);
    expect(v.phase).toBe("working");
    expect(v.stalled).toBe(false);
  });

  it("working while reasoning streams without visible text", () => {
    const m = marksAtTurnStart();
    applyVitalNotification(
      m,
      { method: "item/reasoning/textDelta" },
      T0 + 6_000,
    );
    const v = classify(m, T0 + 6_500);
    expect(v.phase).toBe("working");
  });

  it("slow after prolonged total silence with nothing running", () => {
    const m = marksAtTurnStart();
    applyVitalNotification(m, { method: "item/agentMessage/delta" }, T0 + 500);
    // 11s since any activity, no running tool → ambiguous stall.
    const v = classify(m, T0 + 11_500);
    expect(v.phase).toBe("slow");
    expect(v.stalled).toBe(true);
    expect(v.sinceActivityMs).toBe(11_000);
  });

  it("a heartbeat keeps the turn alive without pretending the model responded", () => {
    const m = marksAtTurnStart();
    applyVitalNotification(
      m,
      { method: "turn/heartbeat", params: { elapsedS: 30 } },
      T0 + 30_000,
    );
    const v = classify(m, T0 + 31_000); // 1s since heartbeat
    expect(v.phase).toBe("waiting");
    expect(v.stalled).toBe(false);
  });

  it("exposes elapsed + TTFT + worst-gap metrics", () => {
    const m = marksAtTurnStart();
    applyVitalNotification(m, { method: "item/agentMessage/delta" }, T0 + 700);
    applyVitalNotification(
      m,
      { method: "item/agentMessage/delta" },
      T0 + 3_200,
    );
    const v = classify(m, T0 + 3_400);
    expect(v.ttftMs).toBe(700);
    expect(v.maxDeltaGapMs).toBe(2_500);
    expect(v.elapsedMs).toBe(3_400);
  });

  it("threshold boundary: exactly streamingFreshMs is no longer streaming", () => {
    const m = marksAtTurnStart();
    applyVitalNotification(m, { method: "item/agentMessage/delta" }, T0);
    const v = classify(m, T0 + DEFAULT_VITALS_THRESHOLDS.streamingFreshMs);
    expect(v.phase).not.toBe("streaming");
  });
});
