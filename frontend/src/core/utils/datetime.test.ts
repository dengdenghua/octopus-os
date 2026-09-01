import { afterEach, describe, expect, test, vi } from "vitest";

import {
  formatCompactRelativeTimestamp,
  formatRelativeTimestamp,
} from "./datetime";

describe("compact relative timestamps", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  test("uses short month-day labels for old Chinese sidebar dates", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 9, 9, 0, 0));

    const oldDate = new Date(2026, 5, 30, 9, 0, 0);

    expect(formatRelativeTimestamp(oldDate, "zh-CN")).toBe("2026年06月30日");
    expect(formatCompactRelativeTimestamp(oldDate, "zh-CN")).toBe("6/30");
  });

  test("uses compact recent relative labels", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 9, 9, 0, 0));

    const recentDate = new Date(2026, 6, 9, 8, 42, 0);

    expect(formatCompactRelativeTimestamp(recentDate, "zh-CN")).toBe("18m");
    expect(formatCompactRelativeTimestamp(recentDate, "en-US")).toBe("18m");
  });

  test("does not throw for an unfinished thread timestamp", () => {
    expect(formatCompactRelativeTimestamp("", "zh-CN")).toBe("");
    expect(formatRelativeTimestamp("not-a-date", "zh-CN")).toBe("");
  });
});
