import { describe, expect, it } from "vitest";

import { workspaceArtifactsQueryKey } from "./use-workspace-artifacts";

describe("workspace artifact query keys", () => {
  it("matches the shared actor and thread namespace", () => {
    expect(workspaceArtifactsQueryKey("thread-1", "alice")).toEqual([
      "workspace-artifacts",
      "alice",
      "thread-1",
    ]);
  });
});
