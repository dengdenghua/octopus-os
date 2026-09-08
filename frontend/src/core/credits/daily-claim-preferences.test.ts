import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ actor: "account-a" }));
vi.mock("@/core/auth/api", () => ({ currentActorId: () => identity.actor }));

import {
  dailyClaimDismissalStorageKey,
  markDailyClaimDismissedToday,
  wasDailyClaimDismissedToday,
} from "./daily-claim-preferences";

describe("daily claim dismissal preferences", () => {
  beforeEach(() => {
    localStorage.clear();
    identity.actor = "account-a";
  });

  it("keeps today's dismissal separate per actor and date", () => {
    const date = new Date(2026, 8, 8, 20, 0);
    localStorage.setItem("oct:dailyClaimDismissed:2026-09-08", "1");
    expect(wasDailyClaimDismissedToday(date)).toBe(true);
    expect(
      localStorage.getItem(dailyClaimDismissalStorageKey(date, "account-a")),
    ).toBe("1");

    localStorage.clear();
    expect(dailyClaimDismissalStorageKey(date, "account-a")).not.toBe(
      dailyClaimDismissalStorageKey(date, "account-b"),
    );
    markDailyClaimDismissedToday(date);
    expect(wasDailyClaimDismissedToday(date)).toBe(true);

    identity.actor = "account-b";
    expect(wasDailyClaimDismissedToday(date)).toBe(false);
    expect(wasDailyClaimDismissedToday(new Date(2026, 8, 9, 8, 0))).toBe(false);
  });
});
