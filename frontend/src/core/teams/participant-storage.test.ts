import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ actor: "alice" }));
vi.mock("@/core/auth/api", () => ({
  authHeaders: () => ({}),
  currentActorId: () => identity.actor,
  jsonAuthHeaders: () => ({ "Content-Type": "application/json" }),
}));

import { readOrCreateTeamParticipantId } from "./api";

describe("team participant storage", () => {
  beforeEach(() => {
    identity.actor = "alice";
    localStorage.clear();
  });

  it("keeps the participant identity per account", () => {
    const alice = readOrCreateTeamParticipantId();
    identity.actor = "bob";
    const bob = readOrCreateTeamParticipantId();

    expect(alice).not.toBe(bob);
    identity.actor = "alice";
    expect(readOrCreateTeamParticipantId()).toBe(alice);
  });

  it("migrates the legacy participant key once", () => {
    localStorage.setItem("echo:teamParticipantId", "legacy-seat");

    expect(readOrCreateTeamParticipantId()).toBe("legacy-seat");
    expect(localStorage.getItem("echo:teamParticipantId")).toBeNull();
    expect(localStorage.getItem("echo:teamParticipantId:alice")).toBe(
      "legacy-seat",
    );
  });
});
