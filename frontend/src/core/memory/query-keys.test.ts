import { describe, expect, it } from "vitest";

import {
  memoryAssetTraceQueryKey,
  memoryAssetsQueryKey,
  memoryConfigQueryKey,
  memoryQueryKey,
  memorySearchQueryKey,
} from "./hooks";

describe("memory query scopes", () => {
  it("keeps personal memory and asset queries in the actor namespace", () => {
    expect(memoryQueryKey("alice")).toEqual(["memory", "alice"]);
    expect(memoryConfigQueryKey("alice")).toEqual(["memory-config", "alice"]);
    expect(memoryAssetsQueryKey({ limit: 10 }, "alice")).toEqual([
      "memory-assets",
      "alice",
      { limit: 10 },
    ]);
    expect(memoryAssetTraceQueryKey("asset-1", "alice")).toEqual([
      "memory-asset-trace",
      "alice",
      "asset-1",
    ]);
    expect(memorySearchQueryKey("q", 20, "alice")).toEqual([
      "memory-search",
      "alice",
      "q",
      20,
    ]);
    expect(memoryQueryKey("alice")).not.toEqual(memoryQueryKey("bob"));
  });
});
