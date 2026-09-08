import { beforeEach, describe, expect, it, vi } from "vitest";

const actor = vi.hoisted(() => ({ value: "alice" }));

vi.mock("@/core/auth/api", () => ({
  currentActorId: () => actor.value,
}));

const modules = await import("./enabled-modules");

describe("account-scoped module preferences", () => {
  beforeEach(() => {
    localStorage.clear();
    actor.value = "alice";
    modules.resetModuleStateCache();
  });

  it("keeps sidebar preferences separate for the same persona across accounts", () => {
    modules.setModuleEnabled("community", false, "general");
    expect(modules.userEnabledModuleIds("general")).not.toContain("community");

    actor.value = "bob";
    expect(modules.userEnabledModuleIds("general")).toContain("community");

    actor.value = "alice";
    expect(modules.userEnabledModuleIds("general")).not.toContain("community");
    expect(
      localStorage.getItem("echo.modules.persona-overrides.v1:actor:alice"),
    ).toContain("community");
  });

  it("migrates a legacy preference to the first authenticated account", () => {
    localStorage.setItem(
      "echo.modules.persona-overrides.v1",
      JSON.stringify({ general: { community: false } }),
    );

    expect(modules.userEnabledModuleIds("general")).not.toContain("community");
    expect(
      localStorage.getItem("echo.modules.persona-overrides.v1"),
    ).toBeNull();
    actor.value = "bob";
    expect(modules.userEnabledModuleIds("general")).toContain("community");
  });
});
