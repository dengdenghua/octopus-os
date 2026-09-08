import { describe, expect, it } from "vitest";

import {
  intelligenceReportsQueryKey,
  intelligenceSubscriptionsQueryKey,
} from "./query-keys";

describe("intelligence query keys", () => {
  it("keeps account-owned intelligence caches isolated", () => {
    expect(intelligenceSubscriptionsQueryKey("alice")).toEqual([
      "intelligence",
      "alice",
      "subscriptions",
    ]);
    expect(intelligenceReportsQueryKey("alice")).toEqual([
      "intelligence",
      "alice",
      "reports",
    ]);
    expect(intelligenceReportsQueryKey("alice")).not.toEqual(
      intelligenceReportsQueryKey("bob"),
    );
  });
});
