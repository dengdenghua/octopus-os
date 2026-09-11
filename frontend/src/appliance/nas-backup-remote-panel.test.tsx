import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NasBackupRemotePanel } from "./nas-backup-remote-panel";

const fetchRemotes = vi.fn();
const planRemote = vi.fn();
const applyRemote = vi.fn();
const requestApproval = vi.fn();

vi.mock("@/appliance/nas-backup", () => ({
  fetchNasBackupRemotes: (...args: unknown[]) => fetchRemotes(...args),
  planNasBackupRemote: (...args: unknown[]) => planRemote(...args),
  applyNasBackupRemote: (...args: unknown[]) => applyRemote(...args),
}));

vi.mock("@/appliance/approval", () => ({
  requestHighRiskApproval: (...args: unknown[]) => requestApproval(...args),
}));

const plan = {
  schema: "echo.nas-backup-remote-plan.v1",
  planId: "a".repeat(64),
  operation: "create",
  requiresApproval: true,
  desired: {
    operation: "create",
    remoteId: "offsite",
    kind: "s3",
    label: "异地对象存储",
  },
  mountpoint: "/mnt/echo-backup-remotes/offsite",
  pathsRedacted: true,
  secretsRedacted: true,
};

beforeEach(() => {
  vi.clearAllMocks();
  fetchRemotes.mockResolvedValue({
    schema: "echo.nas-backup-remote-status.v1",
    remotes: [],
    count: 0,
    pathsRedacted: true,
    secretsRedacted: true,
  });
  planRemote.mockResolvedValue(plan);
  requestApproval.mockResolvedValue({ approvalToken: "approval-once" });
  applyRemote.mockResolvedValue({
    ...plan,
    applied: true,
    verified: true,
    mounted: true,
  });
});

describe("NasBackupRemotePanel", () => {
  it("creates an encrypted S3-compatible mount and selects it for backup", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    render(<NasBackupRemotePanel onChanged={onChanged} />);
    await waitFor(() => expect(fetchRemotes).toHaveBeenCalled());

    await user.type(screen.getByLabelText("远端 ID"), "offsite");
    await user.type(screen.getByLabelText("显示名称"), "异地对象存储");
    await user.type(
      screen.getByLabelText("HTTPS S3 端点"),
      "https://s3.example.test",
    );
    await user.type(screen.getByLabelText("存储桶"), "echo-backups");
    await user.clear(screen.getByLabelText("桶内前缀"));
    await user.type(screen.getByLabelText("桶内前缀"), "family/nas");
    await user.type(screen.getByLabelText("S3 Access Key"), "ACCESS-KEY");
    await user.type(screen.getByLabelText("S3 Secret Key"), "private-secret");
    await user.click(
      screen.getByRole("button", { name: "预览并建立加密挂载" }),
    );

    await waitFor(() =>
      expect(planRemote).toHaveBeenCalledWith(
        expect.objectContaining({
          remoteId: "offsite",
          endpoint: "https://s3.example.test",
          secretAccessKey: "private-secret",
        }),
      ),
    );
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    await user.type(screen.getByLabelText("设备管理员密码"), "admin-password");
    await user.click(screen.getByRole("button", { name: "确认建立" }));

    await waitFor(() =>
      expect(requestApproval).toHaveBeenCalledWith(
        "storage.nas-backup.remote.configure",
        plan.planId,
        "admin-password",
      ),
    );
    expect(applyRemote).toHaveBeenCalledWith(
      expect.objectContaining({ secretAccessKey: "private-secret" }),
      plan.planId,
      "approval-once",
    );
    await waitFor(() =>
      expect(onChanged).toHaveBeenCalledWith(
        "/mnt/echo-backup-remotes/offsite",
      ),
    );
    expect(screen.getByLabelText("S3 Secret Key")).toHaveValue("");
  });

  it("requires approval before removing a configured remote", async () => {
    const user = userEvent.setup();
    fetchRemotes.mockResolvedValue({
      schema: "echo.nas-backup-remote-status.v1",
      remotes: [
        { id: "offsite", label: "异地对象存储", kind: "s3", mounted: true },
      ],
      count: 1,
      pathsRedacted: true,
      secretsRedacted: true,
    });
    planRemote.mockResolvedValue({
      ...plan,
      operation: "remove",
      desired: { operation: "remove", remoteId: "offsite", kind: "s3" },
    });
    render(<NasBackupRemotePanel />);

    await screen.findByText(/已安全挂载/);
    await user.click(screen.getByRole("button", { name: "移除" }));
    await user.type(screen.getByLabelText("设备管理员密码"), "admin-password");
    await user.click(screen.getByRole("button", { name: "确认移除" }));

    await waitFor(() =>
      expect(applyRemote).toHaveBeenCalledWith(
        {
          schema: "echo.nas-backup-remote-desired.v1",
          operation: "remove",
          remoteId: "offsite",
        },
        plan.planId,
        "approval-once",
      ),
    );
  });
});
