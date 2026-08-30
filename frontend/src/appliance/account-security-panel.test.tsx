import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { revokeAllSessions, rotateAdminPassword } from "./account-security";
import { AccountSecurityPanel } from "./account-security-panel";
import { requestHighRiskApproval } from "./approval";
import {
  fetchOmvFilesystems,
  fetchOmvHealth,
  fetchOmvSharePrivileges,
  fetchOmvSharingOverview,
  fetchOmvSmartDevices,
  fetchOmvStorageTopology,
  fetchOmvStatus,
} from "./omv";

vi.mock("./account-security", () => ({
  revokeAllSessions: vi.fn(),
  rotateAdminPassword: vi.fn(),
}));

vi.mock("./approval", () => ({
  requestHighRiskApproval: vi.fn(),
}));

vi.mock("./omv", () => ({
  fetchOmvFilesystems: vi.fn(),
  fetchOmvHealth: vi.fn(),
  fetchOmvSharePrivileges: vi.fn(),
  fetchOmvSharingOverview: vi.fn(),
  fetchOmvSmart: vi.fn(),
  fetchOmvSmartDevices: vi.fn(),
  fetchOmvStorageTopology: vi.fn(),
  fetchOmvStatus: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(requestHighRiskApproval).mockResolvedValue({
    approvalToken: "one-shot.signature",
    expiresIn: 90,
    action: "credentials.rotate",
    target: "admin",
  });
  vi.mocked(rotateAdminPassword).mockResolvedValue({
    success: true,
    sessionsRevoked: true,
    sessionNotBefore: 42,
  });
  vi.mocked(revokeAllSessions).mockResolvedValue({
    success: true,
    sessionsRevoked: true,
    sessionNotBefore: 43,
  });
  vi.mocked(fetchOmvStatus).mockResolvedValue({
    configured: true,
    available: true,
    readOnly: true,
    adminUrl: "https://nas.example.test",
  });
  vi.mocked(fetchOmvFilesystems).mockResolvedValue([]);
  vi.mocked(fetchOmvHealth).mockResolvedValue({
    schemaVersion: 1,
    state: "healthy",
    stale: false,
    checkedAt: "2026-08-26T01:00:00Z",
    lastSuccessfulAt: "2026-08-26T01:00:00Z",
    intervalSeconds: 300,
    persistenceHealthy: true,
    monitoring: true,
    activeAlerts: [],
    events: [],
    summary: { critical: 0, warning: 0, total: 0 },
    readOnly: true,
  });
  vi.mocked(fetchOmvSmartDevices).mockResolvedValue([]);
  vi.mocked(fetchOmvStorageTopology).mockResolvedValue({
    devices: [],
    arrays: [],
  });
  vi.mocked(fetchOmvSharingOverview).mockResolvedValue({
    sharedFolders: [],
    sharedFolderTargets: [],
    users: [],
    groups: [],
    smb: { enabled: true, shares: [] },
    nfs: { enabled: false, shares: [] },
  });
  vi.mocked(fetchOmvSharePrivileges).mockResolvedValue([]);
});

describe("Echo OS account security settings", () => {
  it("rotates the password through a password-bound approval", async () => {
    const user = userEvent.setup();
    const onSessionEnded = vi.fn();
    render(
      <AccountSecurityPanel
        open
        onClose={vi.fn()}
        onSessionEnded={onSessionEnded}
      />,
    );

    await user.type(screen.getByLabelText("当前密码"), "current-device-pass");
    await user.type(screen.getByLabelText("新密码"), "replacement-device-pass");
    await user.type(
      screen.getByLabelText("确认新密码"),
      "replacement-device-pass",
    );
    await user.click(screen.getByRole("button", { name: "更新密码" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "credentials.rotate",
        "admin",
        "current-device-pass",
      ),
    );
    expect(rotateAdminPassword).toHaveBeenCalledWith(
      "replacement-device-pass",
      "one-shot.signature",
    );
    expect(onSessionEnded).toHaveBeenCalledWith(
      "管理员密码已更新，请使用新密码重新登录",
    );
  });

  it("requires a second password check before signing out every device", async () => {
    const user = userEvent.setup();
    const onSessionEnded = vi.fn();
    vi.mocked(requestHighRiskApproval).mockResolvedValue({
      approvalToken: "revoke.signature",
      expiresIn: 90,
      action: "sessions.revoke",
      target: "all",
    });
    render(
      <AccountSecurityPanel
        open
        onClose={vi.fn()}
        onSessionEnded={onSessionEnded}
      />,
    );

    await user.click(screen.getByRole("button", { name: "全部退出…" }));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    await user.type(screen.getByLabelText("设备管理员密码"), "device-pass");
    await user.click(screen.getByRole("button", { name: "全部退出" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "sessions.revoke",
        "all",
        "device-pass",
      ),
    );
    expect(revokeAllSessions).toHaveBeenCalledWith("revoke.signature");
    expect(onSessionEnded).toHaveBeenCalledWith(
      "所有设备会话都已退出，请重新登录",
    );
  });

  it("opens read-only storage health inside system settings", async () => {
    const user = userEvent.setup();
    render(
      <AccountSecurityPanel open onClose={vi.fn()} onSessionEnded={vi.fn()} />,
    );

    await user.click(screen.getByRole("button", { name: "存储健康" }));

    expect(
      await screen.findByRole("heading", { name: "存储健康" }),
    ).toBeInTheDocument();
    expect(fetchOmvStatus).toHaveBeenCalledOnce();
  });

  it("can open directly on storage health from a recovery action", async () => {
    render(
      <AccountSecurityPanel
        open
        initialSection="storage"
        onClose={vi.fn()}
        onSessionEnded={vi.fn()}
      />,
    );

    expect(
      await screen.findByRole("heading", { name: "存储健康" }),
    ).toBeInTheDocument();
    expect(fetchOmvStatus).toHaveBeenCalled();
  });

  it("opens the OMV-backed sharing and user overview", async () => {
    const user = userEvent.setup();
    render(
      <AccountSecurityPanel open onClose={vi.fn()} onSessionEnded={vi.fn()} />,
    );

    await user.click(screen.getByRole("button", { name: "共享与用户" }));

    expect(
      await screen.findByRole("heading", { name: "共享与用户" }),
    ).toBeInTheDocument();
    expect(fetchOmvSharingOverview).toHaveBeenCalledOnce();
  });
});
