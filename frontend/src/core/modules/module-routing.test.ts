import { describe, expect, it } from "vitest";

import { MODULE_CATALOG, moduleById, pinnedModuleIds } from "./catalog";
import {
  filterRoutesByEnabled,
  isLocationBlocked,
  moduleForLocation,
} from "./module-routing";

describe("module catalog", () => {
  it("has unique ids", () => {
    const ids = MODULE_CATALOG.map((m) => m.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("keeps at least one pinned entry so the sidebar can't be emptied", () => {
    expect(pinnedModuleIds().length).toBeGreaterThan(0);
  });

  it("resolves ids back to descriptors", () => {
    expect(moduleById("knowledge")?.labelKey).toBe("navKnowledgeGraph");
    expect(moduleById("nope")).toBeUndefined();
  });
});

describe("moduleForLocation", () => {
  it("matches a plain route", () => {
    expect(moduleForLocation("/workspace/community", "")?.id).toBe("community");
  });

  it("distinguishes storage libraries by the library param", () => {
    expect(
      moduleForLocation("/workspace/storage", "?library=docs")?.id,
    ).toBe("library.docs");
    expect(
      moduleForLocation("/workspace/storage", "?library=videos")?.id,
    ).toBe("library.videos");
  });

  it("matches no module for a storage URL without a library param", () => {
    expect(moduleForLocation("/workspace/storage", "")).toBeUndefined();
  });

  it("returns undefined for routes outside the catalog", () => {
    expect(moduleForLocation("/workspace/realtime/abc123", "")).toBeUndefined();
    expect(moduleForLocation("/login", "")).toBeUndefined();
  });

  it("matches nested paths under a module root", () => {
    expect(moduleForLocation("/workspace/community/post/1", "")?.id).toBe(
      "community",
    );
  });
});

describe("isLocationBlocked", () => {
  const all = MODULE_CATALOG.map((m) => m.id);

  it("passes when the owning module is enabled", () => {
    expect(isLocationBlocked("/workspace/community", "", all)).toBe(false);
  });

  it("blocks when the owning module is disabled", () => {
    const without = all.filter((id) => id !== "community");
    expect(isLocationBlocked("/workspace/community", "", without)).toBe(true);
  });

  it("never blocks routes outside the catalog", () => {
    expect(isLocationBlocked("/workspace/realtime/x", "", [])).toBe(false);
  });

  it("blocks one storage library without touching its siblings", () => {
    const without = all.filter((id) => id !== "library.images");
    expect(isLocationBlocked("/workspace/storage", "?library=images", without)).toBe(
      true,
    );
    expect(isLocationBlocked("/workspace/storage", "?library=docs", without)).toBe(
      false,
    );
  });
});

describe("filterRoutesByEnabled", () => {
  it("drops disabled entries and keeps order", () => {
    const routes = [
      { to: "/workspace/knowledge?surface=chat" },
      { to: "/workspace/storage?surface=company&library=apps" },
      { to: "/workspace/storage?surface=company&library=docs" },
    ];
    const enabled = MODULE_CATALOG.map((m) => m.id).filter(
      (id) => id !== "library.apps",
    );
    expect(filterRoutesByEnabled(routes, enabled).map((r) => r.to)).toEqual([
      "/workspace/knowledge?surface=chat",
      "/workspace/storage?surface=company&library=docs",
    ]);
  });

  it("keeps pinned entries even when absent from the enabled list", () => {
    const pinned = MODULE_CATALOG.find((m) => !m.removable);
    expect(pinned).toBeDefined();
    expect(filterRoutesByEnabled([{ to: pinned!.to }], [])).toHaveLength(1);
  });

  it("keeps routes that aren't catalog modules", () => {
    const routes = [{ to: "/workspace/some-structural-route" }];
    expect(filterRoutesByEnabled(routes, [])).toHaveLength(1);
  });
});
