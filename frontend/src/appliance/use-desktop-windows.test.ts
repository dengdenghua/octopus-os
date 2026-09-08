import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { useDesktopWindows } from "./use-desktop-windows";

const actor = vi.hoisted(() => ({ value: "one" }));
vi.mock("@/core/auth/api", () => ({
  currentActorId: () => actor.value,
}));

beforeEach(() => {
  actor.value = "one";
});

it("reuses a created task window when the task panel opens the same thread", () => {
  const { result } = renderHook(useDesktopWindows);
  act(() =>
    result.current.openWindow({
      id: "assistant",
      title: "助手",
      url: "/workspace/realtime/new",
    }),
  );
  act(() =>
    result.current.reportWindowRoute(
      "assistant",
      "/workspace/realtime/thread-1?agent=coder",
      "/workspace/realtime/thread-1?agent=coder",
    ),
  );
  act(() => result.current.minimizeWindow("assistant"));
  act(() => {
    result.current.openWindow({
      id: "task-record-A",
      threadId: "thread-1",
      title: "任务",
      url: "/workspace/realtime/thread-1",
    });
    result.current.openWindow({
      id: "task-record-B",
      threadId: "thread-1",
      title: "任务",
      url: "/workspace/realtime/thread-1",
    });
  });
  expect(result.current.windows).toHaveLength(1);
  expect(result.current.focusedWin).toBe("assistant");
  expect(result.current.minimized.size).toBe(0);
  expect(result.current.windows[0]?.url).toContain("agent=coder");
});

it("moves focus to a visible window after minimizing or closing the focused task", () => {
  const { result } = renderHook(useDesktopWindows);
  act(() => {
    result.current.openWindow({ id: "first", title: "1", url: "/1" });
    result.current.openWindow({ id: "second", title: "2", url: "/2" });
    result.current.minimizeWindow("second");
  });
  expect(result.current.focusedWin).toBe("first");
  act(() => result.current.closeWindow("first"));
  expect(result.current.focusedWin).toBeNull();
});

it("clears windows immediately when the authenticated actor changes", () => {
  const { result, rerender } = renderHook(useDesktopWindows);
  act(() =>
    result.current.openWindow({
      id: "previous-account-task",
      title: "上一账号任务",
      url: "/workspace/realtime/previous",
    }),
  );
  expect(result.current.windows).toHaveLength(1);

  actor.value = "two";
  rerender();

  expect(result.current.windows).toEqual([]);
  expect(result.current.minimized.size).toBe(0);
  expect(result.current.focusedWin).toBeNull();
});
