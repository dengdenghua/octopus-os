import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  fetchBtrfsSnapshots,
  planBtrfsSnapshot,
  planBtrfsSnapshotDelete,
} from "./btrfs-snapshots";
import { BtrfsSnapshotPanel } from "./btrfs-snapshot-panel";

vi.mock("./btrfs-snapshots", () => ({
  applyBtrfsSnapshot: vi.fn(),
  applyBtrfsSnapshotDelete: vi.fn(),
  fetchBtrfsSnapshots: vi.fn(),
  planBtrfsSnapshot: vi.fn(),
  planBtrfsSnapshotDelete: vi.fn(),
}));

const share = "11111111-2222-4333-8444-555555555555";
const snapshot = {
  snapshotId: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
  name: "before_upgrade",
  subvolumeUuid: "12345678-1234-4234-9234-123456789abc",
  readOnly: true as const,
};

describe("BtrfsSnapshotPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchBtrfsSnapshots).mockResolvedValue({
      sharedFolderRef: share,
      snapshots: [snapshot],
      limit: 256,
    });
  });

  it("states the crash-consistency boundary and previews portable names", async () => {
    vi.mocked(planBtrfsSnapshot).mockResolvedValue({
      schema: "echo.omv.btrfs-snapshot-plan.v1",
      planId: "a".repeat(64),
      operation: "create",
      requiresApproval: true,
      desired: {
        schema: "echo.omv.btrfs-snapshot-desired.v1",
        sharedFolderRef: share,
        name: "nightly_1",
      },
      snapshot: null,
      safety: {
        readOnly: true,
        sameFilesystem: true,
        applicationQuiesce: false,
        maximumPerShare: 256,
        restoreSupported: false,
      },
    });
    render(
      <BtrfsSnapshotPanel
        sharedFolderRef={share}
        sharedFolderName="Photos"
        canCreate
        canDelete
      />,
    );
    expect(await screen.findByText(/崩溃一致/)).toBeInTheDocument();
    await userEvent.type(
      screen.getByLabelText("Photos 的快照名称"),
      "nightly_1",
    );
    await userEvent.click(screen.getByRole("button", { name: "预览创建" }));
    await waitFor(() =>
      expect(planBtrfsSnapshot).toHaveBeenCalledWith({
        schema: "echo.omv.btrfs-snapshot-desired.v1",
        sharedFolderRef: share,
        name: "nightly_1",
      }),
    );
    expect(screen.getByText(/将创建只读快照 nightly_1/)).toBeInTheDocument();
  });

  it("previews deletion by opaque snapshot id before opening approval", async () => {
    vi.mocked(planBtrfsSnapshotDelete).mockResolvedValue({
      schema: "echo.omv.btrfs-snapshot-delete-plan.v1",
      planId: "b".repeat(64),
      operation: "delete",
      requiresApproval: true,
      desired: {
        schema: "echo.omv.btrfs-snapshot-delete-desired.v1",
        sharedFolderRef: share,
        snapshotId: snapshot.snapshotId,
      },
      snapshot,
    });
    render(
      <BtrfsSnapshotPanel
        sharedFolderRef={share}
        sharedFolderName="Photos"
        canCreate
        canDelete
      />,
    );
    await screen.findByText("before_upgrade");
    await userEvent.click(
      screen.getByRole("button", { name: "删除快照 before_upgrade" }),
    );
    await waitFor(() =>
      expect(planBtrfsSnapshotDelete).toHaveBeenCalledWith({
        schema: "echo.omv.btrfs-snapshot-delete-desired.v1",
        sharedFolderRef: share,
        snapshotId: snapshot.snapshotId,
      }),
    );
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  });
});
