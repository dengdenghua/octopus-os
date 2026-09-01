import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  consumePendingNewSession,
  isThreadStale,
  writePendingNewSession,
} from "./pending-new-session";

describe("isThreadStale", () => {
  const now = Date.now();
  const hoursAgo = (h: number) => new Date(now - h * 3_600_000).toISOString();

  it("treats 0 / negative threshold as never stale (feature disabled)", () => {
    expect(isThreadStale(hoursAgo(10), 0)).toBe(false);
    expect(isThreadStale(hoursAgo(10), -1)).toBe(false);
  });

  it("treats missing / garbage timestamps as not stale", () => {
    expect(isThreadStale(undefined, 6)).toBe(false);
    expect(isThreadStale(null, 6)).toBe(false);
    expect(isThreadStale("not-a-date", 6)).toBe(false);
  });

  it("is stale only when idle exceeds the threshold", () => {
    expect(isThreadStale(hoursAgo(7), 6)).toBe(true);
    expect(isThreadStale(hoursAgo(5), 6)).toBe(false);
    // Boundary at exactly N hours is flaky against wall-clock drift, so anchor
    // both sides to the same `Date.now()` the function reads, with a margin.
    const base = Date.now();
    const justUnder = new Date(base - 6 * 3_600_000 + 2000).toISOString();
    const justOver = new Date(base - 6 * 3_600_000 - 2000).toISOString();
    expect(isThreadStale(justUnder, 6)).toBe(false);
    expect(isThreadStale(justOver, 6)).toBe(true);
  });
});

describe("pending-new-session hand-off", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });
  afterEach(() => {
    window.sessionStorage.clear();
  });

  it("round-trips a written hand-off and clears it on consume", () => {
    writePendingNewSession("hello world");
    expect(window.sessionStorage.getItem("echo:pending-new-session")).not.toBeNull();
    const text = consumePendingNewSession();
    expect(text).toBe("hello world");
    // Consumed → cleared, so a second consume returns null.
    expect(consumePendingNewSession()).toBeNull();
  });

  it("ignores empty / whitespace-only payloads", () => {
    writePendingNewSession("   ");
    expect(consumePendingNewSession()).toBeNull();
  });

  it("discards hand-offs older than the max age", () => {
    // Simulate an old, valid payload by writing it directly.
    const stale = JSON.stringify({
      text: "old",
      ts: Date.now() - 120_000,
    });
    window.sessionStorage.setItem("echo:pending-new-session", stale);
    expect(consumePendingNewSession()).toBeNull();
    // And the stale entry is removed.
    expect(window.sessionStorage.getItem("echo:pending-new-session")).toBeNull();
  });

  it("returns null when nothing is pending", () => {
    expect(consumePendingNewSession()).toBeNull();
  });
});
