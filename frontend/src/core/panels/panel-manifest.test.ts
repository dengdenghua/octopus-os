import { describe, expect, it } from "vitest";

import {
  getPanel,
  getPanelVersion,
  listPanels,
  registerPanel,
  resetPanelsForTests,
  type PanelManifest,
} from "./panel-manifest";

function stub(
  id: string,
  overrides: Partial<PanelManifest> = {},
): PanelManifest {
  return {
    id,
    title: id,
    zone: "workbench",
    component: () => null,
    ...overrides,
  };
}

describe("PanelManifest registry", () => {
  it("registers and resolves a panel", () => {
    resetPanelsForTests();
    const panel = stub("workbench.alpha");
    registerPanel(panel);
    expect(getPanel("workbench.alpha")).toBe(panel);
  });

  it("rejects duplicate ids", () => {
    resetPanelsForTests();
    registerPanel(stub("dup"));
    expect(() => registerPanel(stub("dup"))).toThrow(/duplicate panel id/);
  });

  it("lists panels sorted by order then id", () => {
    resetPanelsForTests();
    registerPanel(stub("b", { order: 2 }));
    registerPanel(stub("a", { order: 1 }));
    registerPanel(stub("c"));
    expect(listPanels().map((p) => p.id)).toEqual(["c", "a", "b"]);
  });

  it("filters by zone and permission", () => {
    resetPanelsForTests();
    registerPanel(stub("w1", { zone: "workspace" }));
    registerPanel(stub("w2", { zone: "workspace", permission: "admin" }));
    registerPanel(stub("s1", { zone: "settings" }));
    expect(listPanels({ zone: "workspace" }).map((p) => p.id)).toEqual([
      "w1",
      "w2",
    ]);
    expect(
      listPanels({ zone: "workspace", permission: "admin" }).map((p) => p.id),
    ).toEqual(["w2"]);
  });

  it("bumps the version on registration and reset", () => {
    resetPanelsForTests();
    const v0 = getPanelVersion();
    registerPanel(stub("x"));
    expect(getPanelVersion()).toBeGreaterThan(v0);
    resetPanelsForTests();
    expect(getPanelVersion()).toBeGreaterThan(v0);
  });
});
