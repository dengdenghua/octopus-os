import { describe, expect, it } from "vitest";

import {
  createLocalCreativeProject,
  creativeCanvasStorageKey,
  creativeProjectsStorageKey,
  readLocalCreativeProjects,
} from "./local-projects";

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
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
    expect(creativeCanvasStorageKey("canvas", "luna", null)).toBe(
      "canvas:creation:luna:room",
    );
    expect(creativeCanvasStorageKey("canvas", "luna", "p-1")).toBe(
      "canvas:creation:luna:project:p-1",
    );
  });
});
