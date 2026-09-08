import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ actor: "account-a" }));

vi.mock("@/core/auth/api", () => ({
  currentActorId: () => identity.actor,
}));

import {
  claimDailySignIn,
  getCommunityCredits,
  spendMarketCredits,
} from "./ledger";

describe("community credit ledger", () => {
  beforeEach(() => {
    identity.actor = "account-a";
    window.localStorage.clear();
  });

  it("keeps balances isolated between accounts", () => {
    expect(getCommunityCredits()).toBe(100);
    expect(claimDailySignIn()).toEqual({ claimed: true, amount: 10 });
    expect(getCommunityCredits()).toBe(110);

    identity.actor = "account-b";
    expect(getCommunityCredits()).toBe(100);
    expect(spendMarketCredits("demo", 50)).toBe(true);
    expect(getCommunityCredits()).toBe(50);

    identity.actor = "account-a";
    expect(getCommunityCredits()).toBe(110);
  });
});
