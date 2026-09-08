import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ actor: "account-a" }));

vi.mock("@/core/auth/api", () => ({
  currentActorId: () => identity.actor,
}));

import {
  DEFAULT_ASSISTANT_NAME,
  getAssistantDisplayName,
  setAssistantDisplayName,
} from "./assistant-naming";

describe("assistant naming", () => {
  beforeEach(() => {
    identity.actor = "account-a";
    window.localStorage.clear();
  });

  it("keeps assistant nicknames isolated between accounts", () => {
    setAssistantDisplayName("Ari");
    identity.actor = "account-b";
    expect(getAssistantDisplayName()).toBe(DEFAULT_ASSISTANT_NAME);
    setAssistantDisplayName("Bea");
    identity.actor = "account-a";
    expect(getAssistantDisplayName()).toBe("Ari");
  });

  it("migrates a legacy device nickname into the signed-in account", () => {
    window.localStorage.setItem("echo.assistant-name", "Legacy Echo");

    expect(getAssistantDisplayName()).toBe("Legacy Echo");
    expect(window.localStorage.getItem("echo.assistant-name")).toBeNull();
    expect(window.localStorage.getItem("echo.assistant-name:account-a")).toBe(
      "Legacy Echo",
    );
  });
});
