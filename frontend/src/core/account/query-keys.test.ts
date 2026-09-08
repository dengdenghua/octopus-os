import { describe, expect, it, vi } from "vitest";

const actor = vi.hoisted(() => vi.fn(() => "account-a"));

vi.mock("@/core/auth/api", () => ({
  currentActorId: actor,
}));

import { queryKeys } from "./query-keys";

describe("account query keys", () => {
  it("separates account-owned caches by actor", () => {
    expect(queryKeys.usage()).toEqual(["account", "account-a", "usage"]);
    expect(queryKeys.profile()).toEqual(["account", "account-a", "profile"]);

    actor.mockReturnValue("account-b");

    expect(queryKeys.usage()).toEqual(["account", "account-b", "usage"]);
    expect(queryKeys.usage()).not.toEqual(
      expect.arrayContaining(["account-a"]),
    );
  });

  it("keeps the public plans catalog shared", () => {
    actor.mockReturnValue("account-a");
    expect(queryKeys.plans()).toEqual([
      "account",
      "plans",
      { includeInactive: false },
    ]);
  });
});
