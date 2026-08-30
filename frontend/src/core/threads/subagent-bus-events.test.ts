import { describe, expect, it } from "vitest";

import {
  busEventToLiveEvent,
  SUBAGENT_BUS_TYPES,
  type SubAgentBusEvent,
} from "./subagent-bus-events";

function ev(
  type: string,
  payload: Record<string, unknown>,
  seq = 1,
): SubAgentBusEvent {
  return {
    type,
    thread_id: "child",
    root_thread_id: "root",
    seq,
    ts: 1720000000,
    payload,
  };
}

describe("busEventToLiveEvent", () => {
  it("covers all typed lifecycle events", () => {
    expect(SUBAGENT_BUS_TYPES).toEqual(
      new Set([
        "sub_started",
        "sub_tool_start",
        "sub_tool_end",
        "sub_concluded",
        "sub_incomplete",
        "sub_failed",
      ]),
    );
  });

  it("maps sub_started to a spawned lifecycle tile", () => {
    const e = busEventToLiveEvent(
      ev("sub_started", { role: "researcher", codename: "exp", avatar: "🔎" }),
      0,
    );
    expect(e).toMatchObject({
      lifecycle: "spawned",
      status: "running",
      name: "exp",
      subagentCodename: "exp",
      subagentAvatar: "🔎",
      subAgentRole: "researcher",
    });
  });

  it("maps sub_tool_start/end to running→done", () => {
    const s = busEventToLiveEvent(
      ev("sub_tool_start", {
        role: "researcher",
        tool: "web_search",
        iteration: 1,
      }),
      0,
    );
    expect(s).toMatchObject({
      status: "running",
      name: "web_search",
      subAgentRole: "researcher",
    });
    const e = busEventToLiveEvent(
      ev("sub_tool_end", {
        role: "researcher",
        tool: "web_search",
        iteration: 1,
        status: "success",
        duration_ms: 12,
      }),
      1,
    );
    expect(e).toMatchObject({ status: "done", durationMs: 12 });
  });

  it("groups every child event under its own codename lane", () => {
    const spawnA = busEventToLiveEvent(
      ev("sub_started", { role: "researcher", codename: "Spark-A" }, 1),
      0,
    );
    const toolA = busEventToLiveEvent(
      ev(
        "sub_tool_end",
        {
          role: "researcher",
          codename: "Spark-A",
          tool: "web_search",
          status: "success",
        },
        2,
      ),
      1,
    );
    const spawnB = busEventToLiveEvent(
      ev("sub_started", { role: "researcher", codename: "Spark-B" }, 3),
      2,
    );
    // Same role, different codenames -> distinct per-child group keys.
    expect(spawnA?.agentId).toBe("Spark-A");
    expect(toolA?.agentId).toBe("Spark-A");
    expect(spawnB?.agentId).toBe("Spark-B");
    // Tool events no longer carry the parent's tool-call id, so grouped
    // timeline keys on the child (codename) instead of merging siblings.
    expect(toolA?.parentToolUseId).toBeUndefined();
  });

  it("maps sub_tool_end error", () => {
    const e = busEventToLiveEvent(
      ev("sub_tool_end", { role: "r", tool: "x", status: "error" }),
      0,
    );
    expect(e?.status).toBe("error");
  });

  it("maps sub_concluded done", () => {
    const e = busEventToLiveEvent(
      ev("sub_concluded", { role: "researcher", ok: true, iteration_count: 4 }),
      0,
    );
    expect(e).toMatchObject({
      lifecycle: "finished",
      status: "done",
      iterationCount: 4,
    });
  });

  it("maps sub_incomplete to an explicit incomplete error (not success)", () => {
    const e = busEventToLiveEvent(
      ev("sub_incomplete", {
        role: "explorer",
        reason: "round_cap",
        rounds: 35,
      }),
      0,
    );
    expect(e).toMatchObject({ lifecycle: "finished", status: "error" });
    expect(e?.error).toContain("未完成");
    expect(e?.error).toContain("35");
  });

  it("maps sub_failed to error", () => {
    const e = busEventToLiveEvent(
      ev("sub_failed", { role: "explorer", ok: false, error: "boom" }),
      0,
    );
    expect(e).toMatchObject({
      lifecycle: "finished",
      status: "error",
      error: "boom",
    });
  });

  it("ignores unknown event types", () => {
    expect(busEventToLiveEvent(ev("sub_unknown", { role: "r" }), 0)).toBeNull();
  });
});
