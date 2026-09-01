/**
 * Tests for EventBus type-safe event system.
 */

import { renderHook } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { eventBus, useEvent } from "./event-bus";

describe("EventBus", () => {
  beforeEach(() => {
    eventBus.clear();
  });

  it("should subscribe and emit events", () => {
    const handler = vi.fn();
    eventBus.on("agent:changed", handler);
    eventBus.emit("agent:changed", { name: "test-agent" });

    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler).toHaveBeenCalledWith({ name: "test-agent" });
  });

  it("should support multiple listeners for same event", () => {
    const handler1 = vi.fn();
    const handler2 = vi.fn();
    eventBus.on("settings:changed", handler1);
    eventBus.on("settings:changed", handler2);
    eventBus.emit("settings:changed");

    expect(handler1).toHaveBeenCalledTimes(1);
    expect(handler2).toHaveBeenCalledTimes(1);
  });

  it("should unsubscribe correctly", () => {
    const handler = vi.fn();
    const unsubscribe = eventBus.on("projects:changed", handler);
    unsubscribe();
    eventBus.emit("projects:changed");

    expect(handler).not.toHaveBeenCalled();
  });

  it("should support once listener", () => {
    const handler = vi.fn();
    eventBus.once("team:select", handler);
    eventBus.emit("team:select", { id: "1", name: "Team A" });
    eventBus.emit("team:select", { id: "2", name: "Team B" });

    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler).toHaveBeenCalledWith({ id: "1", name: "Team A" });
  });

  it("should handle void events without payload", () => {
    const handler = vi.fn();
    eventBus.on("ui:toggle-sidebar", handler);
    eventBus.emit("ui:toggle-sidebar");

    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler).toHaveBeenCalledWith(undefined);
  });

  it("should not crash when emitting with no listeners", () => {
    expect(() => {
      eventBus.emit("workspace:changed");
    }).not.toThrow();
  });

  it("should return listener count", () => {
    expect(eventBus.listenerCount()).toBe(0);

    const unsub1 = eventBus.on("agent:changed", vi.fn());
    const unsub2 = eventBus.on("settings:changed", vi.fn());
    expect(eventBus.listenerCount()).toBe(2);

    unsub1();
    expect(eventBus.listenerCount()).toBe(1);

    unsub2();
    expect(eventBus.listenerCount()).toBe(0);
  });

  it("should return per-event listener count", () => {
    eventBus.on("agent:changed", vi.fn());
    eventBus.on("agent:changed", vi.fn());
    eventBus.on("settings:changed", vi.fn());

    expect(eventBus.listenerCount("agent:changed")).toBe(2);
    expect(eventBus.listenerCount("settings:changed")).toBe(1);
    expect(eventBus.listenerCount("projects:changed")).toBe(0);
  });

  it("should clear all listeners", () => {
    eventBus.on("agent:changed", vi.fn());
    eventBus.on("settings:changed", vi.fn());
    eventBus.clear();

    expect(eventBus.listenerCount()).toBe(0);
  });

  it("should handle errors in listeners gracefully", () => {
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const badHandler = vi.fn().mockImplementation(() => {
      throw new Error("Listener error");
    });
    const goodHandler = vi.fn();

    eventBus.on("agent:changed", badHandler);
    eventBus.on("agent:changed", goodHandler);
    eventBus.emit("agent:changed", { name: "test" });

    expect(badHandler).toHaveBeenCalled();
    expect(goodHandler).toHaveBeenCalled();
    expect(consoleSpy).toHaveBeenCalled();

    consoleSpy.mockRestore();
  });

  it("should handle complex payload types", () => {
    const handler = vi.fn();
    eventBus.on("react:step", handler);

    const payload = {
      taskId: "task-1",
      threadId: "thread-1",
      currentPhase: "planning",
      workingSet: [
        {
          path: "/test/file.py",
          last_read_at: 1234567890,
          last_modified_at: 1234567890,
          tokens_estimated: 100,
          relevance: "high",
        },
      ],
      progressSummary: "50% complete",
      feedbackSummary: null,
      thinkingPlan: null,
    };

    eventBus.emit("react:step", payload);
    expect(handler).toHaveBeenCalledWith(payload);
  });

  it("delivers events to the latest render without forcing a resubscribe", () => {
    const received: string[] = [];
    const { rerender, unmount } = renderHook(
      ({ label }) => {
        useEvent("ui:open-settings", ({ tab }) => {
          received.push(`${label}:${tab ?? "appearance"}`);
        });
      },
      { initialProps: { label: "closed" } },
    );

    eventBus.emit("ui:open-settings", { tab: "models" });
    rerender({ label: "mobile-open" });
    eventBus.emit("ui:open-settings", { tab: "privacy" });

    expect(received).toEqual(["closed:models", "mobile-open:privacy"]);
    expect(eventBus.listenerCount("ui:open-settings")).toBe(1);

    unmount();
    expect(eventBus.listenerCount("ui:open-settings")).toBe(0);
  });
});
