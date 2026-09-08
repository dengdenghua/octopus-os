import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ actor: "account-a" }));

vi.mock("@/core/auth/api", () => ({
  currentActorId: () => identity.actor,
}));

import {
  preferredWorkbenchTab,
  rememberedWorkbenchTab,
  rememberWorkbenchTab,
} from "./workbench-preferences";

describe("persona workbench preferences", () => {
  beforeEach(() => {
    identity.actor = "account-a";
    window.localStorage.clear();
  });

  it("uses each persona's workbench default", () => {
    expect(preferredWorkbenchTab("coder", false)).toBe("terminal");
    expect(preferredWorkbenchTab("desktop_operator", false)).toBe("browser");
    expect(preferredWorkbenchTab("market_researcher", false)).toBe("workspace");
    expect(preferredWorkbenchTab("aoi", false)).toBe("workspace");
  });

  it("always gives a bound project the highest priority", () => {
    rememberWorkbenchTab("general", "browser");
    expect(preferredWorkbenchTab("general", true)).toBe("project");
  });

  it("keeps manual choices isolated by persona", () => {
    rememberWorkbenchTab("coder", "browser");
    rememberWorkbenchTab("aoi", "agent");

    expect(rememberedWorkbenchTab("coder")).toBe("browser");
    expect(rememberedWorkbenchTab("aoi")).toBe("agent");
    expect(rememberedWorkbenchTab("market_researcher")).toBeNull();
  });

  it("ignores transient tabs that should not become persona defaults", () => {
    rememberWorkbenchTab("general", "project");
    rememberWorkbenchTab("general", "artifacts");
    expect(rememberedWorkbenchTab("general")).toBeNull();
  });

  it("keeps tab choices isolated between accounts", () => {
    rememberWorkbenchTab("general", "browser");
    identity.actor = "account-b";
    expect(rememberedWorkbenchTab("general")).toBeNull();
    rememberWorkbenchTab("general", "terminal");
    identity.actor = "account-a";
    expect(rememberedWorkbenchTab("general")).toBe("browser");
  });
});
