import { describe, expect, it } from "vitest";

import {
  preserveWorkbenchPresentation,
  workbenchRoute,
} from "./desktop-workspace-route";

describe("workspace presentation routing", () => {
  it("preserves workbench presentation for internal redirects", () => {
    expect(
      preserveWorkbenchPresentation(
        "/workspace/realtime/new",
        "?presentation=workbench",
      ),
    ).toBe("/workspace/realtime/new?presentation=workbench");
  });

  it("keeps explicit presentation choices and external routes unchanged", () => {
    expect(
      preserveWorkbenchPresentation(
        "/workspace/projects?presentation=standalone",
        "?presentation=workbench",
      ),
    ).toBe("/workspace/projects?presentation=standalone");
    expect(
      preserveWorkbenchPresentation("/browser", "?presentation=workbench"),
    ).toBe("/browser");
  });

  it("continues to produce a workbench route from a workspace route", () => {
    expect(workbenchRoute("/workspace/projects")).toBe(
      "/workspace/projects?presentation=workbench",
    );
  });
});
