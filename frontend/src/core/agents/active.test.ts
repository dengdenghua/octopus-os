import { describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ actor: "account-a" }));

vi.mock("@/core/auth/api", () => ({
  currentActorId: () => identity.actor,
}));

import {
  activeAgentIdForLocation,
  activeAgentStorageKey,
  readStoredActiveAgentId,
} from "./active";

describe("activeAgentIdForLocation", () => {
  it("resolves a stale selection and legacy URL against the server roster", () => {
    for (const search of ["", "?agent=coder", "?agent=desktop_operator"]) {
      expect(
        activeAgentIdForLocation("/workspace/realtime/new", search, "coder", [
          "general",
        ]),
      ).toBe("general");
    }
  });

  it("keeps a selectable persona instead of always forcing the default", () => {
    expect(
      activeAgentIdForLocation(
        "/workspace/realtime/new",
        "?agent=market_researcher",
        "general",
        ["general", "market_researcher"],
      ),
    ).toBe("market_researcher");
  });

  it("does not invent a selectable persona when the roster has none", () => {
    expect(
      activeAgentIdForLocation(
        "/workspace/realtime/new",
        "?agent=coder",
        "general",
        [],
      ),
    ).toBeNull();
    expect(
      activeAgentIdForLocation("/workspace/realtime/new", "", "general", [
        "valuation-analyst",
      ]),
    ).toBeNull();
  });

  it("uses the fresh-task query persona before the stored persona", () => {
    expect(
      activeAgentIdForLocation(
        "/workspace/realtime/new",
        "?agent=market_researcher",
        "general",
      ),
    ).toBe("market_researcher");
  });

  it("keeps a historical thread on the persisted persona until its owner loads", () => {
    expect(
      activeAgentIdForLocation(
        "/workspace/realtime/thread-1",
        "?agent=market_researcher",
        "general",
      ),
    ).toBe("general");
  });

  it("does not promote an on-demand expert to a primary persona", () => {
    expect(
      activeAgentIdForLocation(
        "/workspace/realtime/new",
        "?agent=valuation-analyst",
        "general",
      ),
    ).toBe("general");
  });
});

describe("active agent persistence", () => {
  it("uses a separate storage slot for each account", () => {
    const first = activeAgentStorageKey();
    identity.actor = "account-b";
    expect(activeAgentStorageKey()).not.toBe(first);
    identity.actor = "account-a";
  });

  it("migrates a legacy device preference for a signed-in account", () => {
    window.localStorage.setItem("echo.active-agent", "coder");

    expect(readStoredActiveAgentId()).toBe("coder");
    expect(window.localStorage.getItem("echo.active-agent")).toBeNull();
    expect(window.localStorage.getItem(activeAgentStorageKey())).toBe("coder");
  });
});
