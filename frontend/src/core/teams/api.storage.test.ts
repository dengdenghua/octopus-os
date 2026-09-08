import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ actor: "account-a" }));

vi.mock("@/core/auth/api", () => ({
  authHeaders: () => ({}),
  jsonAuthHeaders: () => ({ "Content-Type": "application/json" }),
  currentActorId: () => identity.actor,
}));

import { readPreferredTeamId, writePreferredTeam } from "./api";

describe("team preference storage", () => {
  beforeEach(() => {
    identity.actor = "account-a";
    window.localStorage.clear();
  });

  it("keeps the selected team isolated between accounts", () => {
    writePreferredTeam({
      id: "team-a",
      name: "A",
      members: [],
      leaderId: null,
    });
    expect(readPreferredTeamId()).toBe("team-a");

    identity.actor = "account-b";
    expect(readPreferredTeamId()).toBeNull();
  });
});
