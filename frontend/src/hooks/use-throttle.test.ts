import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useThrottle } from "./use-throttle";

describe("useThrottle", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns the initial value immediately", () => {
    const { result } = renderHook(({ value }) => useThrottle(value, 300), {
      initialProps: { value: "a" },
    });
    expect(result.current).toBe("a");
  });

  it("does not update before the throttle limit", () => {
    const { result, rerender } = renderHook(
      ({ value }) => useThrottle(value, 300),
      {
        initialProps: { value: "a" },
      },
    );

    rerender({ value: "b" });
    act(() => {
      vi.advanceTimersByTime(299);
    });
    expect(result.current).toBe("a");
  });

  it("updates after the throttle limit", () => {
    const { result, rerender } = renderHook(
      ({ value }) => useThrottle(value, 300),
      {
        initialProps: { value: "a" },
      },
    );

    rerender({ value: "b" });
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(result.current).toBe("b");
  });

  it("keeps the latest value when multiple changes happen within the same window", () => {
    const { result, rerender } = renderHook(
      ({ value }) => useThrottle(value, 300),
      {
        initialProps: { value: "a" },
      },
    );

    rerender({ value: "b" });
    act(() => {
      vi.advanceTimersByTime(100);
    });
    rerender({ value: "c" });
    act(() => {
      vi.advanceTimersByTime(100);
    });
    rerender({ value: "d" });

    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current).toBe("d");
  });
});
