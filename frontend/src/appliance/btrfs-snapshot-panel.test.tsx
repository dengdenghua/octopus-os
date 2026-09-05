import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  fetchBtrfsSnapshotSchedule,
  fetchBtrfsSnapshots,
  planBtrfsSnapshot,
  planBtrfsSnapshotDelete,
  planBtrfsSnapshotLock,
  planBtrfsSnapshotRestoreCopy,
  planBtrfsSnapshotSchedule,
} from "./btrfs-snapshots";
import { BtrfsSnapshotPanel } from "./btrfs-snapshot-panel";

vi.mock("./btrfs-snapshots", () => ({
  applyBtrfsSnapshot: vi.fn(),
  applyBtrfsSnapshotDelete: vi.fn(),
  applyBtrfsSnapshotRestoreCopy: vi.fn(),
  applyBtrfsSnapshotSchedule: vi.fn(),
  fetchBtrfsSnapshotSchedule: vi.fn(),
  fetchBtrfsSnapshots: vi.fn(),
  planBtrfsSnapshot: vi.fn(),
  planBtrfsSnapshotDelete: vi.fn(),
  planBtrfsSnapshotLock: vi.fn(),
  planBtrfsSnapshotRestoreCopy: vi.fn(),
  planBtrfsSnapshotSchedule: vi.fn(),
}));

const share = "11111111-2222-4333-8444-555555555555";
const snapshot = {
  snapshotId: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
  name: "before_upgrade",
  subvolumeUuid: "12345678-1234-4234-9234-123456789abc",
  readOnly: true as const,
  kind: "manual" as const,
  locked: false,
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
        canLock={false}
        canRestore={false}
        canSchedule={false}
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
        canLock={false}
        canRestore={false}
        canSchedule={false}
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

  it("previews latest-count retention without including manual snapshots", async () => {
    vi.mocked(fetchBtrfsSnapshotSchedule).mockResolvedValue({
      schemaVersion: 1,
      sharedFolderRef: share,
      enabled: false,
      keepLatest: 8,
      configured: false,
      schedulerInstalled: true,
      schedule: "daily after 02:15 local time, randomized within 45 minutes",
      scope: "automaticSnapshotsOnly",
    });
    vi.mocked(planBtrfsSnapshotSchedule).mockResolvedValue({
      planId: "c".repeat(64),
      operation: "enable",
      requiresApproval: true,
      desired: {
        schema: "echo.btrfs-snapshot-schedule-desired.v1",
        sharedFolderRef: share,
        enabled: true,
        keepLatest: 8,
      },
      schedule: "daily after 02:15 local time, randomized within 45 minutes",
      scope: "automaticSnapshotsOnly",
    });
    render(
      <BtrfsSnapshotPanel
        sharedFolderRef={share}
        sharedFolderName="Photos"
        canCreate
        canDelete
        canLock={false}
        canRestore={false}
        canSchedule
      />,
    );
    await screen.findByText("每日自动快照");
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "预览策略" }));
    await waitFor(() =>
      expect(planBtrfsSnapshotSchedule).toHaveBeenCalledWith({
        schema: "echo.btrfs-snapshot-schedule-desired.v1",
        sharedFolderRef: share,
        enabled: true,
        keepLatest: 8,
      }),
    );
    expect(screen.getByText(/手工快照不受影响/)).toBeInTheDocument();
  });

  it("previews recovery as a new share without replacing the source", async () => {
    vi.mocked(planBtrfsSnapshotRestoreCopy).mockResolvedValue({
      schema: "echo.omv.btrfs-snapshot-restore-copy-plan.v1",
      planId: "d".repeat(64),
      operation: "createRecoveredShare",
      requiresApproval: true,
      desired: {
        schema: "echo.omv.btrfs-snapshot-restore-copy-desired.v1",
        sharedFolderRef: share,
        snapshotId: snapshot.snapshotId,
        name: "Photos_recovered",
      },
      sourceSnapshot: snapshot,
      recoveredShareUuid: "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
      safety: {
        sourceShareUntouched: true,
        sourceSnapshotUntouched: true,
        sameFilesystem: true,
        destinationMustBeAbsent: true,
        writableRecoveredCopy: true,
        applicationQuiesce: false,
        fullVolumeRollback: false,
      },
    });
    render(
      <BtrfsSnapshotPanel
        sharedFolderRef={share}
        sharedFolderName="Photos"
        canCreate
        canDelete
        canLock={false}
        canRestore
        canSchedule={false}
      />,
    );
    await screen.findByText("before_upgrade");
    await userEvent.click(
      screen.getByRole("button", {
        name: "从快照 before_upgrade 创建恢复副本",
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "预览恢复" }));
    await waitFor(() =>
      expect(planBtrfsSnapshotRestoreCopy).toHaveBeenCalledWith({
        schema: "echo.omv.btrfs-snapshot-restore-copy-desired.v1",
        sharedFolderRef: share,
        snapshotId: snapshot.snapshotId,
        name: "Photos_recovered",
      }),
    );
    expect(screen.getByText(/源共享和只读快照保持不变/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "管理员确认" }));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  });

  it("previews a deletion lock before asking for approval", async () => {
    vi.mocked(planBtrfsSnapshotLock).mockResolvedValue({
      schema: "echo.btrfs-snapshot-lock-plan.v1",
      planId: "e".repeat(64),
      operation: "lock",
      requiresApproval: true,
      desired: {
        schema: "echo.btrfs-snapshot-lock-desired.v1",
        sharedFolderRef: share,
        snapshotId: snapshot.snapshotId,
        locked: true,
      },
      snapshot,
      safety: {
        preventsManualDelete: true,
        excludedFromAutomaticRetention: true,
        snapshotDataChanged: false,
      },
    });
    render(
      <BtrfsSnapshotPanel
        sharedFolderRef={share}
        sharedFolderName="Photos"
        canCreate
        canDelete
        canLock
        canRestore={false}
        canSchedule={false}
      />,
    );
    await screen.findByText("before_upgrade");
    await userEvent.click(
      screen.getByRole("button", { name: "锁定快照 before_upgrade" }),
    );
    await waitFor(() =>
      expect(planBtrfsSnapshotLock).toHaveBeenCalledWith({
        schema: "echo.btrfs-snapshot-lock-desired.v1",
        sharedFolderRef: share,
        snapshotId: snapshot.snapshotId,
        locked: true,
      }),
    );
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByText(/自动保留清理都会跳过/)).toBeInTheDocument();
  });
});
