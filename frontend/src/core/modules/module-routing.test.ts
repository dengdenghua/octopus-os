import { describe, expect, it } from "vitest";

import { MODULE_CATALOG, moduleById, pinnedModuleIds } from "./catalog";
import { filterRoutesByEnabled, moduleForLocation } from "./module-routing";

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

  it("maps database categories to the same application", () => {
    expect(moduleForLocation("/workspace/storage", "?library=docs")?.id).toBe(
      "local-database",
    );
    expect(moduleForLocation("/workspace/storage", "?library=videos")?.id).toBe(
      "local-database",
    );
  });

  it("matches the database overview", () => {
    expect(moduleForLocation("/workspace/storage", "")?.id).toBe(
      "local-database",
    );
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

describe("filterRoutesByEnabled", () => {
  it("drops disabled entries and keeps order", () => {
    const routes = [
      { to: "/workspace/knowledge?surface=chat" },
      { to: "/workspace/storage?surface=company" },
    ];
    const enabled = MODULE_CATALOG.map((m) => m.id).filter(
      (id) => id !== "local-database",
    );
    expect(filterRoutesByEnabled(routes, enabled).map((r) => r.to)).toEqual([
      "/workspace/knowledge?surface=chat",
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
