import { describe, expect, it } from "vitest";

import { toolEffectsRefetchInterval } from "./tool-effects-context";

describe("tool effect receipt refresh cadence", () => {
  it("stops global scans for an idle conversation", () => {
    expect(
      toolEffectsRefetchInterval(false, [{ state: "indeterminate" }]),
    ).toBe(false);
  });

  it("polls quickly only while a receipt can still change automatically", () => {
    expect(toolEffectsRefetchInterval(true, [{ state: "started" }])).toBe(
      3_000,
    );
    expect(
      toolEffectsRefetchInterval(true, [{ state: "retry_authorized" }]),
    ).toBe(3_000);
  });

  it("keeps a modest discovery poll for settled or human-review receipts", () => {
    expect(toolEffectsRefetchInterval(true, [{ state: "indeterminate" }])).toBe(
      5_000,
    );
    expect(toolEffectsRefetchInterval(true, [{ state: "committed" }])).toBe(
      5_000,
    );
    expect(toolEffectsRefetchInterval(true, [])).toBe(5_000);
  });
});
