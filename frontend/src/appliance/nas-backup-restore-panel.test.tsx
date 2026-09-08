import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestHighRiskApproval } from "./approval";
import {
  applyNasBackupRestore,
  fetchNasBackupRestoreSets,
  fetchNasBackupRestoreTargets,
  planNasBackupRestore,
} from "./nas-backup";
import { NasBackupRestorePanel } from "./nas-backup-restore-panel";

vi.mock("./approval", () => ({ requestHighRiskApproval: vi.fn() }));
vi.mock("./nas-backup", () => ({
  applyNasBackupRestore: vi.fn(),
  fetchNasBackupRestoreSets: vi.fn(),
  fetchNasBackupRestoreTargets: vi.fn(),
  planNasBackupRestore: vi.fn(),
}));
vi.mock("./high-risk-approval-dialog", () => ({
  HighRiskApprovalDialog: ({
    open,
    confirmLabel,
    onConfirm,
  }: {
    open: boolean;
    confirmLabel: string;
    onConfirm: (password: string) => Promise<void>;
  }) =>
    open ? (
      <button
        type="button"
        onClick={() => void onConfirm("administrator-password")}
      >
        {confirmLabel}
      </button>
    ) : null,
}));

const setId = "11111111-2222-4333-8444-555555555555";
const snapshotId = "a".repeat(64);
const planId = "b".repeat(64);
const confirmation = `RESTORE ECHO NAS SET ${setId} SNAPSHOT ${snapshotId}`;
const sourceRefs = [
  "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
  "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
];
const targetRefs = [
  "cccccccc-dddd-4eee-8fff-aaaaaaaaaaaa",
  "dddddddd-eeee-4fff-8aaa-bbbbbbbbbbbb",
];

const status = {
  schemaVersion: 1 as const,
  configured: true,
  enabled: false,
  repositoryConfigured: true,
  credentialConfigured: true,
  credentialRotationRecoveryPending: false,
  schedulerInstalled: true,
  timerEnabled: false,
  schedule: "daily",
  history: [],
  pathsRedacted: true as const,
  source: "native" as const,
};

const listing = {
  schema: "echo.nas-data-backup-restore-set-list.v1" as const,
  repositoryId: "c".repeat(64),
  setCount: 1,
  sets: [
    {
      setId,
      snapshotId,
      createdAt: "2026-09-08T00:00:00Z",
      manifestSha256: "d".repeat(64),
      memberCount: 2,
      members: sourceRefs.map((sharedFolderRef, index) => ({
        sharedFolderRef,
        filesystemUuid:
          `${index + 1}`.repeat(8) + "-1234-4234-8234-123456789abc",
        snapshotId: targetRefs[index],
      })),
    },
  ],
  truncated: false,
  encrypted: true as const,
  pathsRedacted: true as const,
  verification: "authenticated_index_only" as const,
  restoreMode: "explicit-empty-managed-btrfs-target-mapping" as const,
};

const targetListing = {
  schema: "echo.nas-data-backup-restore-target-list.v1" as const,
  targetCount: 2,
  targets: targetRefs.map((sharedFolderRef, index) => ({
    sharedFolderRef,
    name: `restore-${index + 1}`,
    filesystemUuid: `${index + 7}`.repeat(8) + "-1234-4234-8234-123456789abc",
    empty: true,
  })),
  pathsRedacted: true as const,
};

const plan = {
  schema: "echo.nas-data-backup-restore-plan.v1" as const,
  planId,
  operation: "restoreBackupSet" as const,
  requiresApproval: true as const,
  repositoryId: "c".repeat(64),
  snapshotId,
  setId,
  manifestSha256: "d".repeat(64),
  memberCount: 2,
  members: [],
  confirmation,
  recoveryPending: false,
  pathsRedacted: true as const,
  safety: {
    originalManagedBtrfsTargetsOnly: false as const,
    emptyTargetsRequired: true,
    networkSharesMustBeUnpublished: true as const,
    scheduledWritersMustBeDisabled: true as const,
    fullRepositoryReadBeforePromotion: true as const,
    perShareAtomicPromotion: true as const,
    durableResumeReceipt: true as const,
    replacementDiskMappingSupported: true as const,
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchNasBackupRestoreSets).mockResolvedValue(listing);
  vi.mocked(fetchNasBackupRestoreTargets).mockResolvedValue(targetListing);
  vi.mocked(planNasBackupRestore).mockResolvedValue(plan);
  vi.mocked(requestHighRiskApproval).mockResolvedValue({
    approvalToken: "restore-once",
    expiresIn: 300,
    action: "storage.nas-backup.restore",
    target: planId,
  });
  vi.mocked(applyNasBackupRestore).mockResolvedValue({
    schema: "echo.nas-data-backup-restore-result.v1",
    planId,
    repositoryId: "c".repeat(64),
    snapshotId,
    setId,
    manifestSha256: "d".repeat(64),
    memberCount: 2,
    members: [],
    fullReadVerified: true,
    contentVerified: true,
    pathsRedacted: true,
    phase: "verified",
    recovery: "fresh_restore",
    verified: true,
  });
});

describe("NAS backup restore panel", () => {
  it("keeps restore locked while scheduled backup can write", () => {
    render(
      <NasBackupRestorePanel
        status={{ ...status, enabled: true, timerEnabled: true }}
        repository="/mnt/backup/repository"
        repositoryMount="/mnt/backup"
      />,
    );

    expect(
      screen.getByRole("button", { name: "读取可恢复版本" }),
    ).toBeDisabled();
    expect(screen.getByText(/先停用每日备份/)).toBeInTheDocument();
  });

  it("binds typed confirmation and one-time admin approval to the exact plan", async () => {
    const user = userEvent.setup();
    render(
      <NasBackupRestorePanel
        status={status}
        repository="/mnt/backup/repository"
        repositoryMount="/mnt/backup"
      />,
    );

    await user.click(screen.getByRole("button", { name: "读取可恢复版本" }));
    expect(
      await screen.findByRole("option", { name: /2 个卷/ }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "检查恢复条件" }));
    expect(await screen.findByText(confirmation)).toBeInTheDocument();

    const approve = screen.getByRole("button", { name: "管理员审批并恢复" });
    expect(approve).toBeDisabled();
    await user.type(screen.getByLabelText("NAS 恢复确认句"), confirmation);
    expect(approve).toBeEnabled();
    await user.click(approve);
    await user.click(screen.getByRole("button", { name: "确认恢复" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "storage.nas-backup.restore",
        planId,
        "administrator-password",
      ),
    );
    expect(applyNasBackupRestore).toHaveBeenCalledWith(
      {
        schema: "echo.nas-data-backup-restore-desired.v2",
        selector: setId,
        repository: "/mnt/backup/repository",
        repositoryMount: "/mnt/backup",
        targets: sourceRefs.map((sourceSharedFolderRef, index) => ({
          sourceSharedFolderRef,
          targetSharedFolderRef: targetRefs[index],
        })),
      },
      planId,
      confirmation,
      "restore-once",
    );
    expect(await screen.findByText(/已完成 2 个卷的恢复/)).toBeInTheDocument();
  });

  it("surfaces a durable resume plan instead of starting another restore", async () => {
    const user = userEvent.setup();
    vi.mocked(planNasBackupRestore).mockResolvedValue({
      ...plan,
      operation: "resumeRestore",
      recoveryPending: true,
      safety: { ...plan.safety, emptyTargetsRequired: false },
    });
    render(
      <NasBackupRestorePanel
        status={status}
        repository="/mnt/backup/repository"
        repositoryMount="/mnt/backup"
      />,
    );

    await user.click(screen.getByRole("button", { name: "读取可恢复版本" }));
    await user.click(
      await screen.findByRole("button", { name: "检查恢复条件" }),
    );

    expect(
      await screen.findByText(/未完成的持久化恢复事务/),
    ).toBeInTheDocument();
  });
});
