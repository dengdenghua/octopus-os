import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ actor: "account-a" }));
vi.mock("../auth/api", () => ({ currentActorId: () => identity.actor }));
beforeEach(() => {
  identity.actor = "account-a";
});

import {
  clearThreadModelReferences,
  getLocalSettings,
  getThreadLocalSettings,
  getThreadModelName,
  saveThreadLocalSettings,
  saveThreadModelName,
  saveLocalSettings,
  subscribeLocalSettings,
} from "./local";

const LOCAL_SETTINGS_KEY = "echo.local-settings";

describe("local settings defaults", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("defaults chat capability mode to react", () => {
    expect(getLocalSettings().context.mode).toBe("react");
  });

  it("provides safe personal-space defaults and normalizes invalid stored modes", () => {
    expect(getLocalSettings().personal_space).toEqual({
      default_folder: "",
      default_mode: "general",
      remember_last_mode: true,
      custom_instructions: "",
    });

    localStorage.setItem(
      LOCAL_SETTINGS_KEY,
      JSON.stringify({
        personal_space: {
          default_folder: "  /Users/example/Echo  ",
          default_mode: "unknown",
          custom_instructions: "x".repeat(2100),
        },
      }),
    );

    const settings = getLocalSettings().personal_space;
    expect(settings.default_folder).toBe("/Users/example/Echo");
    expect(settings.default_mode).toBe("general");
    expect(settings.custom_instructions).toHaveLength(2000);
  });

  it("normalizes persisted chat mode to react", () => {
    localStorage.setItem(
      LOCAL_SETTINGS_KEY,
      JSON.stringify({
        context: {
          mode: "chat",
        },
      }),
    );

    expect(getLocalSettings().context.mode).toBe("react");
  });

  it("defaults the model to the auto router sentinel, not a vendor model", () => {
    // A hardcoded concrete model pinned every fresh install to one vendor:
    // deployments whose provider serves other model names rejected the first
    // turn with an HTTP 400 before the user ever opened the picker.
    expect(getLocalSettings().context.model_name).toBe("auto");
  });

  it("migrates the legacy hardcoded claude-opus default to auto", () => {
    localStorage.setItem(
      LOCAL_SETTINGS_KEY,
      JSON.stringify({
        context: {
          model_name: "claude-opus",
        },
      }),
    );

    expect(getLocalSettings().context.model_name).toBe("auto");
  });

  it("keeps an explicit versioned model the user picked", () => {
    localStorage.setItem(
      LOCAL_SETTINGS_KEY,
      JSON.stringify({
        context: {
          model_name: "claude-opus-4-7-20250805",
        },
      }),
    );

    expect(getLocalSettings().context.model_name).toBe(
      "claude-opus-4-7-20250805",
    );
  });

  it("clears only thread overrides that reference a deleted model", () => {
    saveThreadModelName("thread-a", "removed-model");
    saveThreadModelName("thread-b", "kept-model");
    saveThreadModelName("thread-c", "removed-model");

    expect(clearThreadModelReferences("removed-model")).toBe(2);
    expect(getThreadModelName("thread-a")).toBeUndefined();
    expect(getThreadModelName("thread-b")).toBe("kept-model");
    expect(getThreadModelName("thread-c")).toBeUndefined();
  });

  it("isolates system settings between actors", () => {
    saveLocalSettings({
      ...getLocalSettings(),
      context: { ...getLocalSettings().context, model_name: "model-a" },
    });
    identity.actor = "account-b";
    expect(getLocalSettings().context.model_name).toBe("auto");
    identity.actor = "account-a";
    expect(getLocalSettings().context.model_name).toBe("model-a");
  });
});

describe("per-thread model persistence", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("keeps task model and effort across system changes and isolates accounts", () => {
    const base = getLocalSettings();
    saveThreadLocalSettings("task", {
      ...base,
      context: {
        ...base.context,
        model_scope: "task",
        model_name: "task-model",
        reasoning_effort: "high",
      },
    });
    saveLocalSettings({
      ...getLocalSettings(),
      context: {
        ...base.context,
        model_name: "system-model",
        reasoning_effort: "low",
      },
    });
    expect(getThreadLocalSettings("task").context).toMatchObject({
      model_scope: "task",
      model_name: "task-model",
      reasoning_effort: "high",
    });
    expect(getThreadLocalSettings("other").context).toMatchObject({
      model_name: "system-model",
      reasoning_effort: "low",
    });
    identity.actor = "account-b";
    expect(getThreadModelName("task")).toBeUndefined();
    expect(getThreadLocalSettings("task").context.model_scope).not.toBe("task");
    expect(clearThreadModelReferences("task-model")).toBe(0);
    identity.actor = "account-a";
    expect(getThreadModelName("task")).toBe("task-model");
  });

  it("returns a task to the system model and effort explicitly", () => {
    const base = getLocalSettings();
    saveThreadLocalSettings("task", {
      ...base,
      context: {
        ...base.context,
        model_scope: "task",
        model_name: "override",
        reasoning_effort: "high",
      },
    });
    expect(getLocalSettings().context.reasoning_effort).toBeUndefined();
    saveThreadLocalSettings("task", {
      ...base,
      context: { ...base.context, model_scope: "system" },
    });
    expect(getThreadLocalSettings("task").context).toMatchObject({
      model_scope: "system",
      model_name: "auto",
    });
    expect(
      getThreadLocalSettings("task").context.reasoning_effort,
    ).toBeUndefined();
  });

  it("round-trips a model selected in one thread without leaking to others", () => {
    const base = getThreadLocalSettings("thread-a");
    saveThreadLocalSettings("thread-a", {
      ...base,
      context: { ...base.context, model_name: "glm-5.3" },
    });

    expect(getThreadLocalSettings("thread-a").context.model_name).toBe(
      "glm-5.3",
    );
    expect(getLocalSettings().context.model_name).toBe("auto");
    expect(getThreadLocalSettings("thread-b").context.model_name).toBe("auto");
  });

  it("preserves a newer system model when an older thread snapshot is saved", () => {
    const stale = getThreadLocalSettings("thread-a");
    saveLocalSettings({
      ...stale,
      context: { ...stale.context, model_name: "new-system-model" },
    });
    saveThreadLocalSettings("thread-a", {
      ...stale,
      display: { ...stale.display, chat_font_size: "large" },
    });
    expect(getLocalSettings().context.model_name).toBe("new-system-model");
    expect(getThreadLocalSettings("thread-b").context.model_name).toBe(
      "new-system-model",
    );
    expect(getLocalSettings().display.chat_font_size).toBe("large");
  });

  it("refreshes subscribers for cross-tab task overrides and storage clearing", () => {
    let calls = 0;
    const unsubscribe = subscribeLocalSettings(() => {
      calls += 1;
    });
    window.dispatchEvent(
      new StorageEvent("storage", { key: "echo.thread-model.thread-a" }),
    );
    window.dispatchEvent(new StorageEvent("storage", { key: null }));
    window.dispatchEvent(new StorageEvent("storage", { key: "unrelated" }));
    unsubscribe();
    expect(calls).toBe(2);
  });

  it("broadcasts only after the new thread model is observable", () => {
    const observed: Array<string | undefined> = [];
    const unsubscribe = subscribeLocalSettings(() => {
      observed.push(getThreadLocalSettings("thread-a").context.model_name);
    });
    const base = getThreadLocalSettings("thread-a");

    saveThreadLocalSettings("thread-a", {
      ...base,
      context: { ...base.context, model_name: "big-pickle" },
    });
    unsubscribe();

    expect(observed).toEqual(["big-pickle"]);
  });
});
