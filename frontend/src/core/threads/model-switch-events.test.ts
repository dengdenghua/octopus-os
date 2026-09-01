import { beforeEach, describe, expect, it } from "vitest";

import {
  loadModelSwitchEvents,
  recordModelSwitchEvent,
} from "./model-switch-events";

describe("model switch timeline events", () => {
  beforeEach(() => window.localStorage.clear());

  it("persists a switch and restores it for the thread", () => {
    const events = recordModelSwitchEvent("thread-a", [], {
      modelName: "gpt-5.6-sol",
      afterMessageCount: 4,
      now: Date.parse("2026-08-25T10:00:00.000Z"),
    });

    expect(events).toHaveLength(1);
    expect(
      loadModelSwitchEvents("thread-a", Date.parse("2026-08-25T10:01:00.000Z")),
    ).toEqual(events);
    expect(loadModelSwitchEvents("thread-b")).toEqual([]);
  });

  it("collapses repeated picker changes before the next message", () => {
    const first = recordModelSwitchEvent("thread-a", [], {
      modelName: "gpt-5.6-sol",
      afterMessageCount: 4,
      now: 1_000,
    });
    const second = recordModelSwitchEvent("thread-a", first, {
      modelName: "deepseek-v4-flash",
      afterMessageCount: 4,
      now: 2_000,
    });
    const third = recordModelSwitchEvent("thread-a", second, {
      modelName: "gpt-5.6-sol",
      afterMessageCount: 6,
      now: 3_000,
    });

    expect(second.map((event) => event.modelName)).toEqual([
      "deepseek-v4-flash",
    ]);
    expect(third.map((event) => event.modelName)).toEqual([
      "deepseek-v4-flash",
      "gpt-5.6-sol",
    ]);
  });

  it("ignores malformed persisted data", () => {
    window.localStorage.setItem(
      "echo:model-switch-events:thread-a",
      JSON.stringify({ v: 1, events: [{ modelName: "missing fields" }] }),
    );
    expect(loadModelSwitchEvents("thread-a")).toEqual([]);
  });
});
