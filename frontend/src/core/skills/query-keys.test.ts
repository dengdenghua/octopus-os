import { describe, expect, it } from "vitest";

import { skillsQueryKey } from "./hooks";

describe("skill query scope", () => {
  it("keeps enabled skill state in the actor namespace", () => {
    expect(skillsQueryKey("alice")).toEqual(["skills", "alice"]);
    expect(skillsQueryKey("alice")).not.toEqual(skillsQueryKey("bob"));
  });
});
