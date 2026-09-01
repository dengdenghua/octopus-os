import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useLocalStorage } from "./use-local-storage";

const store: Record<string, string> = {};

beforeEach(() => {
  for (const key of Object.keys(store)) delete store[key];

  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => store[key] ?? null),
    setItem: vi.fn((key: string, value: string) => {
      store[key] = value;
    }),
    removeItem: vi.fn((key: string) => {
      delete store[key];
    }),
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useLocalStorage", () => {
  it("returns initial value when nothing stored", () => {
    const { result } = renderHook(() => useLocalStorage("key", "default"));
    expect(result.current[0]).toBe("default");
  });

  it("reads existing value from localStorage", () => {
    store["key"] = JSON.stringify("stored");
    const { result } = renderHook(() => useLocalStorage("key", "default"));
    expect(result.current[0]).toBe("stored");
  });

  it("writes value to localStorage", () => {
    const { result } = renderHook(() => useLocalStorage("key", "default"));
    act(() => {
      result.current[1]("updated");
    });
    expect(result.current[0]).toBe("updated");
    expect(JSON.parse(store["key"])).toBe("updated");
  });

  it("supports updater function", () => {
    const { result } = renderHook(() => useLocalStorage("count", 0));
    act(() => {
      result.current[1]((prev) => prev + 1);
    });
    expect(result.current[0]).toBe(1);
  });

  it("falls back to initial value on JSON parse error", () => {
    store["key"] = "not-valid-json";
    const { result } = renderHook(() => useLocalStorage("key", "fallback"));
    expect(result.current[0]).toBe("fallback");
  });

  it("handles complex objects", () => {
    const obj = { name: "test", items: [1, 2, 3] };
    const { result } = renderHook(() => useLocalStorage("obj", obj));
    expect(result.current[0]).toEqual(obj);
  });
});
