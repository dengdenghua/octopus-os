import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { enUS, jaJP, koKR, zhCN } from "@/core/i18n/locales";

import { defaultEnabledModuleIds, moduleById } from "./catalog";

describe("narrative workbench integration", () => {
  it("ships as a visible, removable workspace module", () => {
    expect(moduleById("narrative")).toMatchObject({
      to: "/workspace/narrative",
      labelKey: "navNarrative",
      group: "workspace",
      section: "chatCapability",
      removable: true,
    });
    expect(defaultEnabledModuleIds()).toContain("narrative");
  });

  it("provides a localized navigation label in every supported locale", () => {
    expect(zhCN.sidebar.navNarrative).toBe("叙事工坊");
    expect(enUS.sidebar.navNarrative).toBe("Narrative Studio");
    expect(jaJP.sidebar.navNarrative).toBe("物語工房");
    expect(koKR.sidebar.navNarrative).toBe("스토리 공방");
  });

  it("keeps the workspace page behind the installed remote surface", () => {
    const routerSource = readFileSync(resolve("src/router.tsx"), "utf8");
    expect(routerSource).toContain('remoteWorkbenchApp("narrative")');
    expect(routerSource).toContain(
      "<RemoteWorkbenchSurface app={NARRATIVE_APP}",
    );
    expect(routerSource).not.toContain(
      'import("./app/workspace/narrative/page")',
    );
  });
});
