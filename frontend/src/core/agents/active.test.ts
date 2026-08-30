import { describe, expect, it } from "vitest";

import { activeAgentIdForLocation } from "./active";

describe("activeAgentIdForLocation", () => {
  it("uses the fresh-task query persona before the stored persona", () => {
    expect(
      activeAgentIdForLocation(
        "/workspace/realtime/new",
        "?agent=market_researcher",
        "general",
      ),
    ).toBe("market_researcher");
  });

  it("keeps a historical thread on the persisted persona until its owner loads", () => {
    expect(
      activeAgentIdForLocation(
        "/workspace/realtime/thread-1",
        "?agent=market_researcher",
        "general",
      ),
    ).toBe("general");
  });

  it("does not promote an on-demand expert to a primary persona", () => {
    expect(
      activeAgentIdForLocation(
        "/workspace/realtime/new",
        "?agent=valuation-analyst",
        "general",
      ),
    ).toBe("general");
  });
});
