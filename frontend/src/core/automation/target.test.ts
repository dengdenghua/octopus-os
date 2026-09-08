import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ actor: "actor-one" }));
vi.mock("@/core/auth/api", () => ({
  currentActorId: () => identity.actor,
}));

import { loadAutomationTarget, saveAutomationTarget } from "./target";

describe("automation target persistence", () => {
  beforeEach(() => {
    window.localStorage.clear();
    identity.actor = "actor-one";
  });

  it("keeps a structured target per conversation", () => {
    saveAutomationTarget("thread-a", {
      kind: "browser_tab",
      source: "browser_relay",
      id: "91",
      title: "Release dashboard",
      url: "https://example.test/releases",
    });

    expect(loadAutomationTarget("thread-a")).toEqual(
      expect.objectContaining({ id: "91", title: "Release dashboard" }),
    );
    expect(loadAutomationTarget("thread-b")).toBeNull();
  });

  it("drops malformed or explicitly cleared values", () => {
    window.localStorage.setItem(
      `echo:automation-target:${encodeURIComponent(identity.actor)}:thread-a`,
      "{}",
    );
    expect(loadAutomationTarget("thread-a")).toBeNull();

    saveAutomationTarget("thread-a", {
      kind: "desktop_window",
      source: "computer",
      id: "window-1",
      title: "Notes",
    });
    saveAutomationTarget("thread-a", null);
    expect(loadAutomationTarget("thread-a")).toBeNull();
  });

  it("does not reuse a browser target after an actor switch", () => {
    saveAutomationTarget("thread-a", {
      kind: "browser_tab",
      source: "browser_relay",
      id: "91",
      title: "Private dashboard",
      url: "https://example.test/private",
    });
    identity.actor = "actor-two";
    expect(loadAutomationTarget("thread-a")).toBeNull();
  });
});
