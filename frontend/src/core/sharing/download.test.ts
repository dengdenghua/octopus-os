import { describe, expect, it } from "vitest";

import { shareSlug } from "./download";

describe("shareSlug", () => {
  it("lowercases and dash-joins ascii words", () => {
    expect(shareSlug("Refactor the Auth Module")).toBe(
      "refactor-the-auth-module",
    );
  });

  it("keeps CJK characters", () => {
    expect(shareSlug("预订机票")).toBe("预订机票");
  });

  it("trims leading/trailing separators and caps length", () => {
    expect(shareSlug("  ***hello*** ")).toBe("hello");
    expect(shareSlug("x".repeat(80)).length).toBe(48);
  });

  it("falls back to 'share' when empty", () => {
    expect(shareSlug("")).toBe("share");
    expect(shareSlug("!!!")).toBe("share");
  });
});
