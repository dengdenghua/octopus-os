import { describe, expect, it } from "vitest";

import { agentQueryKey, agentsQueryKey } from "./hooks";

describe("agent query scopes", () => {
  it("keeps roster and agent details in the actor namespace", () => {
    expect(agentsQueryKey("alice")).toEqual(["agents", "alice"]);
    expect(agentQueryKey("coder", "alice")).toEqual([
      "agents",
      "alice",
      "coder",
    ]);
    expect(agentsQueryKey("alice")).not.toEqual(agentsQueryKey("bob"));
  });
});
