// withActionTimeout: a hung webview IPC promise must settle as a
// rejection instead of freezing the assistant loop in `busy` forever.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  runBrowserActionWithControl,
  withActionTimeout,
  type AgentAction,
} from "./agentic-actions";

describe("withActionTimeout", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("passes a fast result through untouched", async () => {
    const result = withActionTimeout(Promise.resolve("ok"), "click", 1_000);
    await expect(result).resolves.toBe("ok");
  });

  it("rejects a hung promise at the deadline with the action label", async () => {
    const hung = new Promise<never>(() => {});
    const raced = withActionTimeout(hung, "click", 5_000);
    const settled = expect(raced).rejects.toThrow(
      /action timeout \(5s\): click/,
    );
    await vi.advanceTimersByTimeAsync(5_001);
    await settled;
  });

  it("propagates the underlying rejection before the deadline", async () => {
    const failing = Promise.reject(new Error("selector not found"));
    await expect(withActionTimeout(failing, "click", 5_000)).rejects.toThrow(
      "selector not found",
    );
  });

  it("clears the timer when the promise settles first", async () => {
    const spy = vi.spyOn(globalThis, "clearTimeout");
    await withActionTimeout(Promise.resolve(1), "wait", 5_000);
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });
});

describe("runBrowserActionWithControl", () => {
  const action: AgentAction = { type: "click", selector: "#go" };

  it("does not run the action when control was already stopped", async () => {
    const run = vi.fn(async () => ({ action, ok: true }));
    const setIndicator = vi.fn();

    const result = await runBrowserActionWithControl(action, run, {
      surface: "electron_webview",
      targetId: "tab_1",
      getStopped: () => true,
      setIndicator,
    });

    expect(run).not.toHaveBeenCalled();
    expect(result.ok).toBe(false);
    expect(result.error).toContain("operator_stop");
    expect(setIndicator).toHaveBeenCalledWith("paused", {
      action: "click",
      reason: "operator_stop",
    });
  });

  it("shows action edge light while running and idles after success", async () => {
    const setIndicator = vi.fn();

    const result = await runBrowserActionWithControl(
      action,
      async () => ({ action, ok: true, detail: { clicked: true } }),
      {
        surface: "electron_webview",
        targetId: "tab_1",
        getStopped: () => false,
        setIndicator,
      },
    );

    expect(result.ok).toBe(true);
    expect(setIndicator).toHaveBeenNthCalledWith(1, "action", {
      action: "click",
      surface: "electron_webview",
      targetId: "tab_1",
    });
    expect(setIndicator).toHaveBeenLastCalledWith("idle", {
      action: "click",
    });
  });

  it("marks the action interrupted when stop is requested during execution", async () => {
    let stopped = false;
    const setIndicator = vi.fn();

    const result = await runBrowserActionWithControl(
      action,
      async () => {
        stopped = true;
        return { action, ok: true };
      },
      {
        surface: "browser",
        targetId: "tab_2",
        getStopped: () => stopped,
        setIndicator,
      },
    );

    expect(result.ok).toBe(false);
    expect(result.error).toContain("operator_stop");
    expect(setIndicator).toHaveBeenCalledWith("paused", {
      action: "click",
      reason: "operator_stop",
    });
  });
});
