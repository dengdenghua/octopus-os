import { describe, expect, test } from "vitest";

import { getTasksRefetchInterval } from "./hooks";

describe("getTasksRefetchInterval", () => {
  test("stops polling when no task is active", () => {
    expect(getTasksRefetchInterval()).toBe(false);
    expect(getTasksRefetchInterval({ active: [], pending: [] })).toBe(false);
    expect(getTasksRefetchInterval({ paused: [{} as never] })).toBe(false);
    expect(getTasksRefetchInterval({ pending: [{} as never] })).toBe(false);
  });

  test("keeps active tasks responsive", () => {
    expect(getTasksRefetchInterval({ active: [{} as never] })).toBe(2000);
  });
});
