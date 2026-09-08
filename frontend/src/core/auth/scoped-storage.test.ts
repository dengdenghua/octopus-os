import { beforeEach, describe, expect, it } from "vitest";

import {
  actorScopedStorageKey,
  readActorScopedStorageValue,
} from "./scoped-storage";

describe("actor scoped storage", () => {
  beforeEach(() => localStorage.clear());

  it("normalizes blank actors and migrates legacy values only for a named actor", () => {
    localStorage.setItem("echo.preference", "legacy");

    expect(readActorScopedStorageValue("echo.preference", "  ")).toBe("legacy");
    expect(
      localStorage.getItem(actorScopedStorageKey("echo.preference", "")),
    ).toBeNull();
    expect(readActorScopedStorageValue("echo.preference", "account-a")).toBe(
      "legacy",
    );
    expect(
      localStorage.getItem(
        actorScopedStorageKey("echo.preference", "account-a"),
      ),
    ).toBe("legacy");
    expect(localStorage.getItem("echo.preference")).toBeNull();
  });

  it("keeps an explicit empty scoped value from falling back to another actor", () => {
    localStorage.setItem("echo.preference", "legacy");
    localStorage.setItem(
      actorScopedStorageKey("echo.preference", "account-a"),
      "",
    );

    expect(readActorScopedStorageValue("echo.preference", "account-a")).toBe(
      "",
    );
    expect(readActorScopedStorageValue("echo.preference", "account-b")).toBe(
      "legacy",
    );
  });
});
