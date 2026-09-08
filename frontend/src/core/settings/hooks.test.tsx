import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ actor: "account-a" }));
vi.mock("../auth/api", () => ({ currentActorId: () => identity.actor }));

import { useLocalSettings, useThreadSettings } from "./hooks";
import { getLocalSettings, getThreadLocalSettings } from "./local";

beforeEach(() => {
  localStorage.clear();
  identity.actor = "account-a";
});

it("merges simultaneous changes from two mounted system surfaces", () => {
  const first = renderHook(() => useLocalSettings());
  const second = renderHook(() => useLocalSettings());
  act(() => {
    first.result.current[1]("context", { model_name: "new-system-model" });
    second.result.current[1]("display", { chat_font_size: "large" });
  });
  expect(getLocalSettings().context.model_name).toBe("new-system-model");
  expect(getLocalSettings().display.chat_font_size).toBe("large");
  expect(first.result.current[0]).toEqual(second.result.current[0]);
});

it("commits a task selection to its original thread before navigation", () => {
  const hook = renderHook(({ id }) => useThreadSettings(id), {
    initialProps: { id: "first" },
  });
  act(() => {
    hook.result.current[1]("context", {
      model_scope: "task",
      model_name: "task-model",
      reasoning_effort: "high",
    });
    hook.rerender({ id: "second" });
  });
  expect(getThreadLocalSettings("first").context).toMatchObject({
    model_scope: "task",
    model_name: "task-model",
    reasoning_effort: "high",
  });
  expect(getThreadLocalSettings("second").context.model_name).toBe("auto");
  expect(hook.result.current[0].context.model_name).toBe("auto");
});

it("preserves sequential changes made before React renders again", () => {
  const hook = renderHook(() => useThreadSettings("task"));
  act(() => {
    hook.result.current[1]("context", {
      model_scope: "task",
      model_name: "task-model",
    });
    hook.result.current[1]("context", { reasoning_effort: "high" });
  });
  expect(getThreadLocalSettings("task").context).toMatchObject({
    model_scope: "task",
    model_name: "task-model",
    reasoning_effort: "high",
  });
  expect(getLocalSettings().context.reasoning_effort).toBeUndefined();
});

it("reloads settings when the active actor changes", () => {
  const hook = renderHook(() => useLocalSettings());
  act(() => {
    hook.result.current[1]("context", { model_name: "account-a-model" });
  });

  identity.actor = "account-b";
  act(() => hook.rerender());
  expect(hook.result.current[0].context.model_name).toBe("auto");

  act(() => {
    hook.result.current[1]("context", { model_name: "account-b-model" });
  });
  identity.actor = "account-a";
  act(() => hook.rerender());
  expect(hook.result.current[0].context.model_name).toBe("account-a-model");
});

it("reloads thread settings when the active actor changes", () => {
  const hook = renderHook(() => useThreadSettings("shared-thread"));
  act(() => {
    hook.result.current[1]("context", {
      model_scope: "task",
      model_name: "account-a-task-model",
    });
  });

  identity.actor = "account-b";
  act(() => hook.rerender());
  expect(hook.result.current[0].context.model_name).toBe("auto");

  identity.actor = "account-a";
  act(() => hook.rerender());
  expect(hook.result.current[0].context.model_name).toBe(
    "account-a-task-model",
  );
});
