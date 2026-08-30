import { beforeEach, describe, expect, it } from "vitest";

import { MODULE_CATALOG, pinnedModuleIds } from "./catalog";
import {
  enabledModuleIds,
  isModuleEnabled,
  resetModuleStateCache,
  setModuleAvailabilitySnapshot,
  setModuleAvailable,
  setModuleEnabled,
  setModuleStateProvider,
  userEnabledModuleIds,
} from "./enabled-modules";

/** In-memory provider so tests never touch localStorage. */
function memoryProvider(initial: string[] = []) {
  let disabled = [...initial];
  let overrides: Record<string, Record<string, boolean>> = {};
  return {
    readDisabled: () => [...disabled],
    writeDisabled: (ids: string[]) => {
      disabled = [...ids];
    },
    readOverrides: () => structuredClone(overrides),
    writeOverrides: (next: Record<string, Record<string, boolean>>) => {
      overrides = structuredClone(next);
    },
    current: () => [...disabled],
    currentOverrides: () => structuredClone(overrides),
  };
}

describe("enabled modules", () => {
  beforeEach(() => {
    setModuleStateProvider(memoryProvider());
    resetModuleStateCache();
  });

  it("enables everything by default", () => {
    expect(enabledModuleIds()).toEqual(MODULE_CATALOG.map((m) => m.id));
  });

  it("disables a removable module and persists it", () => {
    const provider = memoryProvider();
    setModuleStateProvider(provider);

    setModuleEnabled("community", false);

    expect(isModuleEnabled("community")).toBe(false);
    expect(enabledModuleIds()).not.toContain("community");
    expect(provider.current()).toContain("community");
  });

  it("re-enables a disabled module", () => {
    setModuleStateProvider(memoryProvider(["community"]));
    resetModuleStateCache();
    expect(isModuleEnabled("community")).toBe(false);

    setModuleEnabled("community", true);
    expect(isModuleEnabled("community")).toBe(true);
  });

  it("refuses to disable a pinned module", () => {
    const pinned = pinnedModuleIds()[0];
    setModuleEnabled(pinned, false);
    expect(isModuleEnabled(pinned)).toBe(true);
  });

  it("ignores a pinned id already present in storage", () => {
    const pinned = pinnedModuleIds()[0];
    setModuleStateProvider(memoryProvider([pinned]));
    resetModuleStateCache();
    expect(isModuleEnabled(pinned)).toBe(true);
  });

  it("ignores unknown ids in storage so removed modules can't haunt it", () => {
    setModuleStateProvider(memoryProvider(["deleted.module"]));
    resetModuleStateCache();
    expect(enabledModuleIds()).toEqual(MODULE_CATALOG.map((m) => m.id));
  });

  it("ignores writes for unknown ids", () => {
    setModuleEnabled("not-a-module", false);
    expect(enabledModuleIds()).toEqual(MODULE_CATALOG.map((m) => m.id));
  });

  it("treats modules added after a user's last write as enabled", () => {
    // Persisting a *disabled* list is what buys this: a brand-new catalog
    // entry is absent from storage, so it defaults to visible.
    setModuleStateProvider(memoryProvider(["community"]));
    resetModuleStateCache();
    for (const m of MODULE_CATALOG) {
      if (m.id !== "community") expect(isModuleEnabled(m.id)).toBe(true);
    }
  });

  it("shows paper trading by default only for the market persona", () => {
    expect(enabledModuleIds("market_researcher")).toContain("paper.trading");
    expect(enabledModuleIds("general")).not.toContain("paper.trading");
    expect(enabledModuleIds("coder")).not.toContain("paper.trading");
  });

  it("keeps user module overrides scoped to each persona", () => {
    const provider = memoryProvider();
    setModuleStateProvider(provider);

    setModuleEnabled("paper.trading", true, "general");

    expect(enabledModuleIds("general")).toContain("paper.trading");
    expect(enabledModuleIds("coder")).not.toContain("paper.trading");
    expect(provider.currentOverrides()).toEqual({
      general: { "paper.trading": true },
    });
  });

  it("keeps runtime availability separate from the user's preference", () => {
    setModuleEnabled("narrative", true, "general");
    setModuleAvailabilitySnapshot({ narrative: false });

    expect(enabledModuleIds("general")).not.toContain("narrative");
    expect(userEnabledModuleIds("general")).toContain("narrative");

    setModuleAvailable("narrative", true);
    expect(enabledModuleIds("general")).toContain("narrative");
  });

  it("does not allow persona overrides to resurrect an uninstalled module", () => {
    setModuleEnabled("narrative", true, "writer");
    setModuleAvailabilitySnapshot({ narrative: false });

    expect(enabledModuleIds("writer")).not.toContain("narrative");
  });
});
