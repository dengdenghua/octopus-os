import { describe, expect, it } from "vitest";

import { authorizationsQueryKey } from "./hooks";

describe("integration authorization query scope", () => {
  it("keeps connected app credentials in the actor namespace", () => {
    expect(authorizationsQueryKey("alice")).toEqual([
      "app-authorizations",
      "alice",
    ]);
    expect(authorizationsQueryKey("alice")).not.toEqual(
      authorizationsQueryKey("bob"),
    );
  });
});
