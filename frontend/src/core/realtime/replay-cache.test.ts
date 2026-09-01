// Tests for the local replay cache (P3).
//
// The IndexedDB backend is exercised implicitly in the browser; here we
// pin the in-memory backend's merge/trim semantics (shared contract with
// the IDB one) and the hook's cold-start hydration flow end to end.

import { describe, expect, it } from "vitest";

import { createMemoryReplayCache } from "./replay-cache";
import type { SequencedLoggedEvent } from "./replay";

function evt(
  sequence: number,
  kind = "item_delta",
  payload: Record<string, unknown> = {},
): SequencedLoggedEvent {
  return {
    sequence,
    event: kind,
    eventId: `evt_${sequence}`,
    threadId: "th",
    ts: "2026-07-28T00:00:00Z",
    turnId: "t1",
    payload,
  };
}

describe("createMemoryReplayCache", () => {
  it("returns null for unknown threads", async () => {
    const cache = createMemoryReplayCache();
    expect(await cache.load("nope")).toBeNull();
  });

  it("round-trips appended slices with meta", async () => {
    const cache = createMemoryReplayCache();
    await cache.append("th", [evt(1), evt(2)], {
      streamId: "s1",
      cursor: 2,
    });
    const loaded = await cache.load("th");
    expect(loaded?.events.map((e) => e.sequence)).toEqual([1, 2]);
    expect(loaded?.streamId).toBe("s1");
    expect(loaded?.cursor).toBe(2);
    expect(loaded?.partialFrom).toBe(1);
  });

  it("merges overlapping slices by sequence, sorted", async () => {
    const cache = createMemoryReplayCache();
    await cache.append("th", [evt(1), evt(2), evt(3)], {
      streamId: "s1",
      cursor: 3,
    });
    // Overlapping page re-supplies seq 2-3 and extends to 4-5.
    await cache.append("th", [evt(3), evt(4), evt(5)], {
      streamId: "s1",
      cursor: 5,
    });
    const loaded = await cache.load("th");
    expect(loaded?.events.map((e) => e.sequence)).toEqual([1, 2, 3, 4, 5]);
  });

  it("trims the oldest prefix beyond the cap and advances partialFrom", async () => {
    const cache = createMemoryReplayCache(4);
    await cache.append("th", [evt(1), evt(2), evt(3)], {
      streamId: "s1",
      cursor: 3,
    });
    await cache.append("th", [evt(4), evt(5), evt(6)], {
      streamId: "s1",
      cursor: 6,
    });
    const loaded = await cache.load("th");
    expect(loaded?.events.map((e) => e.sequence)).toEqual([3, 4, 5, 6]);
    expect(loaded?.partialFrom).toBe(3);
    expect(loaded?.cursor).toBe(6);
  });

  it("never lowers the cursor when an older slice arrives late", async () => {
    const cache = createMemoryReplayCache();
    await cache.append("th", [evt(1), evt(2), evt(3)], {
      streamId: "s1",
      cursor: 3,
    });
    await cache.append("th", [evt(2)], { streamId: "s1", cursor: 2 });
    expect((await cache.load("th"))?.cursor).toBe(3);
  });

  it("clear drops events and meta", async () => {
    const cache = createMemoryReplayCache();
    await cache.append("th", [evt(1)], { streamId: "s1", cursor: 1 });
    await cache.clear("th");
    expect(await cache.load("th")).toBeNull();
  });
});
