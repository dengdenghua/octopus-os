import { describe, expect, it } from "vitest";

import {
  createLocalCreativeProject,
  creativeCanvasStorageKey,
  creativeProjectsStorageKey,
  legacyCreativeCanvasStorageKey,
  readCreativeCanvasValue,
  readLocalCreativeProjects,
} from "./local-projects";

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  };
}

describe("local creative projects", () => {
  it("isolates project lists and rooms by persona", () => {
    const storage = memoryStorage();
    createLocalCreativeProject("luna", "品牌片", storage);
    createLocalCreativeProject("kane", "产品页", storage);

    expect(
      readLocalCreativeProjects("luna", storage).map((p) => p.name),
    ).toEqual(["品牌片"]);
    expect(
      readLocalCreativeProjects("kane", storage).map((p) => p.name),
    ).toEqual(["产品页"]);
    expect(creativeProjectsStorageKey("luna")).not.toBe(
      creativeProjectsStorageKey("kane"),
    );
    expect(creativeCanvasStorageKey("canvas", "luna", null, "account-a")).toBe(
      "canvas:creation:luna:room:account-a",
    );
    expect(creativeCanvasStorageKey("canvas", "luna", "p-1", "account-a")).toBe(
      "canvas:creation:luna:project:p-1:account-a",
    );
    expect(legacyCreativeCanvasStorageKey("canvas", "luna", null)).toBe(
      "canvas:creation:luna:room",
    );
  });

  it("keeps creative projects separate by actor and migrates a legacy list", () => {
    const storage = memoryStorage();
    createLocalCreativeProject("luna", "账号 A", storage, "account-a");
    expect(readLocalCreativeProjects("luna", storage, "account-b")).toEqual([]);

    storage.setItem(
      "echo.design.local-projects.v1:luna",
      '[{"id":"old","name":"旧项目"}]',
    );
    expect(
      readLocalCreativeProjects("luna", storage, "account-c"),
    ).toHaveLength(1);
    expect(readLocalCreativeProjects("luna", storage, "account-d")).toEqual([]);
  });

  it("migrates a legacy canvas document once", () => {
    const storage = memoryStorage();
    storage.setItem("canvas:legacy", '{"title":"旧画布"}');

    expect(
      readCreativeCanvasValue(
        "canvas:scoped:account-a",
        "canvas:legacy",
        "account-a",
        storage,
      ),
    ).toBe('{"title":"旧画布"}');
    expect(storage.getItem("canvas:legacy")).toBeNull();
    expect(
      readCreativeCanvasValue(
        "canvas:scoped:account-b",
        "canvas:legacy",
        "account-b",
        storage,
      ),
    ).toBeNull();
  });
});
