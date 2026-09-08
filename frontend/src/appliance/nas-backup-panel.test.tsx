import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestHighRiskApproval } from "./approval";
import {
  applyNasBackupCredential,
  applyNasBackupCredentialRotation,
  applyNasBackupSchedule,
  fetchNasBackupSchedule,
  planNasBackupCredential,
  planNasBackupCredentialRotation,
  planNasBackupSchedule,
} from "./nas-backup";
import { NasBackupPanel } from "./nas-backup-panel";

vi.mock("./approval", () => ({ requestHighRiskApproval: vi.fn() }));
vi.mock("./nas-backup", () => ({
  applyNasBackupCredential: vi.fn(),
  applyNasBackupCredentialRotation: vi.fn(),
  applyNasBackupSchedule: vi.fn(),
  fetchNasBackupSchedule: vi.fn(),
  planNasBackupCredential: vi.fn(),
  planNasBackupCredentialRotation: vi.fn(),
  planNasBackupSchedule: vi.fn(),
}));

const status = {
  schemaVersion: 1 as const,
  configured: false,
  enabled: false,
  repositoryConfigured: false,
  credentialConfigured: true,
  credentialRotationRecoveryPending: false,
  schedulerInstalled: true,
  timerEnabled: false,
  schedule: "daily",
  history: [],
  pathsRedacted: true as const,
  source: "native" as const,
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.mocked(fetchNasBackupSchedule).mockResolvedValue(status);
  vi.mocked(requestHighRiskApproval).mockResolvedValue({
    approvalToken: "backup-once",
    expiresIn: 300,
    action: "storage.nas-backup.schedule",
    target: "a".repeat(64),
  });
});

describe("NAS backup panel", () => {
  it("previews and password-approves an encrypted external backup schedule", async () => {
    const user = userEvent.setup();
    const desired = {
      schema: "echo.nas-data-backup-schedule.v1" as const,
      enabled: true,
      repository: "/mnt/backup/echo-restic",
      repositoryMount: "/mnt/backup",
    };
    vi.mocked(planNasBackupSchedule).mockResolvedValue({
      schema: "echo.nas-data-backup-schedule-plan.v1",
      planId: "a".repeat(64),
      operation: "enable",
      requiresApproval: true,
      current: {
        enabled: false,
        repositoryConfigured: false,
        timerEnabled: false,
      },
      desired: { enabled: true, repositoryConfigured: true },
      schedule: "daily",
      pathsRedacted: true,
      safety: {
        encryptedCredentialRequired: true,
        externalMountedRepositoryRequired: true,
        managedReadOnlyBtrfsSnapshotsOnly: true,
        fullRepositoryReadAfterBackup: true,
      },
    });
    vi.mocked(applyNasBackupSchedule).mockResolvedValue({} as never);
    render(<NasBackupPanel />);

    await user.type(
      await screen.findByLabelText("外部备份盘挂载点"),
      "/mnt/backup",
    );
    await user.type(
      screen.getByLabelText("Restic 仓库目录"),
      "/mnt/backup/echo-restic",
    );
    await user.click(screen.getByRole("button", { name: "预览并启用" }));

    expect(await screen.findByRole("alertdialog")).toHaveTextContent(
      "外部挂载盘和仓库边界",
    );
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "确认启用" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "storage.nas-backup.schedule",
        "a".repeat(64),
        "device-password",
      ),
    );
    expect(applyNasBackupSchedule).toHaveBeenCalledWith(
      desired,
      "a".repeat(64),
      "backup-once",
    );
  });

  it("keeps enable locked when the encrypted credential is absent", async () => {
    vi.mocked(fetchNasBackupSchedule).mockResolvedValue({
      ...status,
      credentialConfigured: false,
    });
    render(<NasBackupPanel />);

    expect(
      await screen.findByText(/请先在上方初始化或连接 Restic 仓库/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "预览并启用" })).toBeDisabled();
  });

  it("locks backup mutations while a power-loss rotation receipt is pending", async () => {
    vi.mocked(fetchNasBackupSchedule).mockResolvedValue({
      ...status,
      credentialRotationRecoveryPending: true,
    });
    render(<NasBackupPanel />);

    expect(
      await screen.findByText(/上次仓库密码轮换仍在安全恢复中/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "预览并启用" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "预览安全轮换" })).toBeDisabled();
  });

  it("provisions an initial host-bound credential without persisting the secret", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchNasBackupSchedule).mockResolvedValue({
      ...status,
      credentialConfigured: false,
    });
    vi.mocked(planNasBackupCredential).mockResolvedValue({
      schema: "echo.nas-data-backup-credential-plan.v1",
      planId: "c".repeat(64),
      operation: "initializeCredential",
      requiresApproval: true,
      desired: {
        mode: "initialize",
        repositoryConfigured: true,
        passwordBound: true,
      },
      pathsRedacted: true,
      safety: {
        systemdEncryptedCredential: true,
        externalMountedRepositoryRequired: true,
        existingCredentialMustBeAbsent: true,
        blindRotationAllowed: false,
      },
    });
    vi.mocked(applyNasBackupCredential).mockResolvedValue({} as never);
    render(<NasBackupPanel />);

    await user.type(
      await screen.findByLabelText("外部备份盘挂载点"),
      "/mnt/backup",
    );
    await user.type(
      screen.getByLabelText("Restic 仓库目录"),
      "/mnt/backup/echo-restic",
    );
    await user.type(
      screen.getByLabelText("仓库加密密码"),
      "correct-horse-battery",
    );
    await user.type(
      screen.getByLabelText("再次输入仓库密码"),
      "correct-horse-battery",
    );
    await user.click(screen.getByRole("button", { name: "初始化并加密保存" }));
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "确认初始化" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "storage.nas-backup.credential.provision",
        "c".repeat(64),
        "device-password",
      ),
    );
    expect(applyNasBackupCredential).toHaveBeenCalledWith(
      {
        schema: "echo.nas-data-backup-credential-desired.v1",
        mode: "initialize",
        repository: "/mnt/backup/echo-restic",
        repositoryMount: "/mnt/backup",
        password: "correct-horse-battery",
      },
      "c".repeat(64),
      "backup-once",
    );
    expect(localStorage.length).toBe(0);
  });

  it("clears the repository secret when credential approval is cancelled", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchNasBackupSchedule).mockResolvedValue({
      ...status,
      credentialConfigured: false,
    });
    vi.mocked(planNasBackupCredential).mockResolvedValue({
      schema: "echo.nas-data-backup-credential-plan.v1",
      planId: "c".repeat(64),
      operation: "initializeCredential",
      requiresApproval: true,
      desired: {
        mode: "initialize",
        repositoryConfigured: true,
        passwordBound: true,
      },
      pathsRedacted: true,
      safety: {
        systemdEncryptedCredential: true,
        externalMountedRepositoryRequired: true,
        existingCredentialMustBeAbsent: true,
        blindRotationAllowed: false,
      },
    });
    render(<NasBackupPanel />);

    await user.type(
      await screen.findByLabelText("外部备份盘挂载点"),
      "/mnt/backup",
    );
    await user.type(
      screen.getByLabelText("Restic 仓库目录"),
      "/mnt/backup/echo-restic",
    );
    await user.type(
      screen.getByLabelText("仓库加密密码"),
      "correct-horse-battery",
    );
    await user.type(
      screen.getByLabelText("再次输入仓库密码"),
      "correct-horse-battery",
    );
    await user.click(screen.getByRole("button", { name: "初始化并加密保存" }));
    await user.click(await screen.findByRole("button", { name: "取消" }));

    expect(screen.getByLabelText("仓库加密密码")).toHaveValue("");
    expect(screen.getByLabelText("再次输入仓库密码")).toHaveValue("");
    expect(applyNasBackupCredential).not.toHaveBeenCalled();
    expect(localStorage.length).toBe(0);
  });

  it("rotates the repository password through an exactly approved plan", async () => {
    const user = userEvent.setup();
    const desired = {
      schema: "echo.nas-data-backup-credential-rotation-desired.v1" as const,
      repository: "/mnt/backup/echo-restic",
      repositoryMount: "/mnt/backup",
      currentPassword: "correct-horse-battery",
      newPassword: "new-correct-horse-battery",
    };
    vi.mocked(planNasBackupCredentialRotation).mockResolvedValue({
      schema: "echo.nas-data-backup-credential-rotation-plan.v1",
      planId: "r".repeat(64),
      operation: "rotateCredential",
      requiresApproval: true,
      desired: {
        repositoryConfigured: true,
        currentPasswordBound: true,
        newPasswordBound: true,
      },
      pathsRedacted: true,
      safety: {
        systemdEncryptedCredential: true,
        newRepositoryKeyVerifiedBeforeSwitch: true,
        oldPasswordKeysRevokedAfterSwitch: true,
        rollbackPreservesRepositoryAccess: true,
      },
    });
    vi.mocked(applyNasBackupCredentialRotation).mockResolvedValue({} as never);
    vi.mocked(requestHighRiskApproval).mockResolvedValue({
      approvalToken: "rotation-once",
      expiresIn: 300,
      action: "storage.nas-backup.credential.rotate",
      target: "r".repeat(64),
    });
    render(<NasBackupPanel />);

    await user.type(
      await screen.findByLabelText("外部备份盘挂载点"),
      "/mnt/backup",
    );
    await user.type(
      screen.getByLabelText("Restic 仓库目录"),
      "/mnt/backup/echo-restic",
    );
    await user.type(
      screen.getByLabelText("当前仓库密码"),
      "correct-horse-battery",
    );
    await user.type(
      screen.getByLabelText("新仓库密码"),
      "new-correct-horse-battery",
    );
    await user.type(
      screen.getByLabelText("再次输入新仓库密码"),
      "new-correct-horse-battery",
    );
    await user.click(screen.getByRole("button", { name: "预览安全轮换" }));

    expect(await screen.findByRole("alertdialog")).toHaveTextContent(
      "先为 Restic 仓库添加并验证新密钥",
    );
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "确认轮换" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "storage.nas-backup.credential.rotate",
        "r".repeat(64),
        "device-password",
      ),
    );
    expect(planNasBackupCredentialRotation).toHaveBeenCalledWith(desired);
    expect(applyNasBackupCredentialRotation).toHaveBeenCalledWith(
      desired,
      "r".repeat(64),
      "rotation-once",
    );
    expect(localStorage.length).toBe(0);
  });

  it("clears both rotation secrets when approval is cancelled", async () => {
    const user = userEvent.setup();
    vi.mocked(planNasBackupCredentialRotation).mockResolvedValue({
      schema: "echo.nas-data-backup-credential-rotation-plan.v1",
      planId: "r".repeat(64),
      operation: "rotateCredential",
      requiresApproval: true,
      desired: {
        repositoryConfigured: true,
        currentPasswordBound: true,
        newPasswordBound: true,
      },
      pathsRedacted: true,
      safety: {
        systemdEncryptedCredential: true,
        newRepositoryKeyVerifiedBeforeSwitch: true,
        oldPasswordKeysRevokedAfterSwitch: true,
        rollbackPreservesRepositoryAccess: true,
      },
    });
    render(<NasBackupPanel />);

    await user.type(
      await screen.findByLabelText("外部备份盘挂载点"),
      "/mnt/backup",
    );
    await user.type(
      screen.getByLabelText("Restic 仓库目录"),
      "/mnt/backup/echo-restic",
    );
    await user.type(
      screen.getByLabelText("当前仓库密码"),
      "correct-horse-battery",
    );
    await user.type(
      screen.getByLabelText("新仓库密码"),
      "new-correct-horse-battery",
    );
    await user.type(
      screen.getByLabelText("再次输入新仓库密码"),
      "new-correct-horse-battery",
    );
    await user.click(screen.getByRole("button", { name: "预览安全轮换" }));
    await user.click(await screen.findByRole("button", { name: "取消" }));

    expect(screen.getByLabelText("当前仓库密码")).toHaveValue("");
    expect(screen.getByLabelText("新仓库密码")).toHaveValue("");
    expect(screen.getByLabelText("再次输入新仓库密码")).toHaveValue("");
    expect(applyNasBackupCredentialRotation).not.toHaveBeenCalled();
    expect(localStorage.length).toBe(0);
  });
});
