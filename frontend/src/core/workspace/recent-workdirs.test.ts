import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ actor: "account-a" }));

vi.mock("@/core/auth/api", () => ({
  currentActorId: () => identity.actor,
}));

import {
  readRecentWorkdirs,
  recentWorkdirsStorageKey,
  writeRecentWorkdirs,
} from "./recent-workdirs";

describe("recent workdir storage", () => {
  beforeEach(() => {
    identity.actor = "account-a";
    window.localStorage.clear();
  });

  it("namespaces paths by actor", () => {
    expect(recentWorkdirsStorageKey()).toBe("echo:recentWorkdirs:account-a");
  });

  it("migrates the legacy device list into the signed-in account", () => {
    window.localStorage.setItem(
      "echo:recentWorkdirs",
      JSON.stringify(["C:/legacy/project"]),
    );

    expect(readRecentWorkdirs()).toEqual(["C:/legacy/project"]);
    expect(window.localStorage.getItem("echo:recentWorkdirs")).toBeNull();
    expect(readRecentWorkdirs("account-b")).toEqual([]);
  });

  it("writes the same account list consumed by every workspace surface", () => {
    writeRecentWorkdirs(["C:/one", "C:/two"]);

    expect(readRecentWorkdirs()).toEqual(["C:/one", "C:/two"]);
    expect(
      window.localStorage.getItem("echo:recentWorkdirs:account-a"),
    ).toContain("C:/one");
  });
});
