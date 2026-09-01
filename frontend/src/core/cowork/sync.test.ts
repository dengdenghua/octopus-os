import { describe, expect, test } from "vitest";

import { buildCoworkSelectionSyncPlan } from "./sync";
import type { CoworkState } from "./types";

function state(ids: string[], mode: CoworkState["mode"] = "cluster"): CoworkState {
  return {
    mode,
    event_count: ids.length,
    is_one_to_one: ids.length <= 1,
    roster: ids.map((id) => ({
      id,
      kind: "agent",
      role: "participant",
      grant: { scope: "all" },
    })),
  };
}

describe("buildCoworkSelectionSyncPlan", () => {
  test("invites the leader and selected collaborators on a fresh thread", () => {
    const plan = buildCoworkSelectionSyncPlan({
      leaderId: "general",
      collaboratorIds: ["codex-cli", "codex-cli"],
      mode: "cluster",
      current: null,
    });

    expect(plan.desiredAgentIds).toEqual(["general", "codex-cli"]);
    expect(plan.inviteAgentIds).toEqual(["general", "codex-cli"]);
    expect(plan.removeAgentIds).toEqual([]);
    expect(plan.mode).toBe("cluster");
    expect(plan.shouldSetMode).toBe(true);
    expect(plan.hasWork).toBe(true);
  });

  test("clearing collaborators removes stored participants and returns to chat", () => {
    const plan = buildCoworkSelectionSyncPlan({
      leaderId: "general",
      collaboratorIds: [],
      mode: "cluster",
      current: state(["general", "codex-cli"], "cluster"),
    });

    expect(plan.desiredAgentIds).toEqual([]);
    expect(plan.inviteAgentIds).toEqual([]);
    expect(plan.removeAgentIds).toEqual(["general", "codex-cli"]);
    expect(plan.mode).toBe("chat");
    expect(plan.shouldSetMode).toBe(true);
  });

  test("does not churn when roster and mode are already current", () => {
    const plan = buildCoworkSelectionSyncPlan({
      leaderId: "general",
      collaboratorIds: ["codex-cli"],
      mode: "swarm",
      current: state(["general", "codex-cli"], "swarm"),
    });

    expect(plan.inviteAgentIds).toEqual([]);
    expect(plan.removeAgentIds).toEqual([]);
    expect(plan.shouldSetMode).toBe(false);
    expect(plan.hasWork).toBe(false);
  });

  test("keeps a sole leader in a durable project group", () => {
    const plan = buildCoworkSelectionSyncPlan({
      leaderId: "general",
      collaboratorIds: [],
      mode: "cluster",
      current: state(["general"], "chat"),
      keepLeader: true,
    });

    expect(plan.desiredAgentIds).toEqual(["general"]);
    expect(plan.removeAgentIds).toEqual([]);
    expect(plan.mode).toBe("chat");
    expect(plan.shouldSetMode).toBe(false);
    expect(plan.hasWork).toBe(false);
  });
});
