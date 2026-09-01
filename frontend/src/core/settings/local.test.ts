import { beforeEach, describe, expect, it } from "vitest";

import {
  clearThreadModelReferences,
  getLocalSettings,
  getThreadLocalSettings,
  getThreadModelName,
  saveThreadLocalSettings,
  saveThreadModelName,
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
});

describe("per-thread model persistence", () => {
  beforeEach(() => {
    localStorage.clear();
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
