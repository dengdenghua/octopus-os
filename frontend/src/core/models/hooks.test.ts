import { describe, expect, it } from "vitest";

import { modelsQueryKey } from "./hooks";

describe("model catalog query scope", () => {
  it("keeps account-owned model catalogs in separate cache namespaces", () => {
    expect(modelsQueryKey("alice")).toEqual(["models", "alice"]);
    expect(modelsQueryKey("bob")).toEqual(["models", "bob"]);
    expect(modelsQueryKey("alice")).not.toEqual(modelsQueryKey("bob"));
  });
});
