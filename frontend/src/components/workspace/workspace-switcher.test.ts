import { describe, expect, test } from "vitest";

import type { Workspace } from "@/core/workspace/types";
import { shouldShowWorkspaceSwitcher } from "./workspace-switcher";

const workspace = (
  id: string,
  mount_type: Workspace["mount_type"],
): Workspace => ({
  id,
  name: id,
  mount_type,
  mount_target: `/${id}`,
  mount_options: null,
  owner_id: "eve",
  created_at: "2026-07-22T00:00:00Z",
});

describe("workspace switcher visibility", () => {
  test("stays out of the shared sidebar until there is a real alternative", () => {
    expect(shouldShowWorkspaceSwitcher([])).toBe(false);
    expect(shouldShowWorkspaceSwitcher([workspace("local", "local")])).toBe(
      false,
    );
  });

  test("appears when a user can actually switch workspaces", () => {
    expect(
      shouldShowWorkspaceSwitcher([
        workspace("local", "local"),
        workspace("remote", "sftp"),
      ]),
    ).toBe(true);
  });
});
