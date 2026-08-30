import { describe, expect, test } from "vitest";

import {
  ACTIVE_ARTIFACT_REFRESH_MS,
  getWorkspaceArtifactRefetchInterval,
} from "./polling";

describe("getWorkspaceArtifactRefetchInterval", () => {
  test("is silent while the conversation is idle", () => {
    expect(getWorkspaceArtifactRefetchInterval(false)).toBe(false);
  });

  test("refreshes artifacts while a turn is running", () => {
    expect(getWorkspaceArtifactRefetchInterval(true)).toBe(
      ACTIVE_ARTIFACT_REFRESH_MS,
    );
  });
});
