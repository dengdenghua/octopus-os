import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  beginAdministratorTotpEnrollment,
  confirmAdministratorTotpEnrollment,
  disableAdministratorTotp,
  fetchAdministratorTotpStatus,
  revokeAllSessions,
  rotateAdminPassword,
} from "./account-security";
import { AccountSecurityPanel } from "./account-security-panel";
import { requestHighRiskApproval } from "./approval";
import {
  fetchNativeFilesystems,
  fetchNativeHealth,
  fetchNativeSmartDevices,
  fetchNativeStorageTopology,
  fetchNativeStatus,
  fetchOmvFilesystems,
  fetchOmvHealth,
  fetchOmvSharePrivileges,
  fetchOmvSharingOverview,
  fetchOmvSmartDevices,
  fetchOmvStorageTopology,
  fetchOmvStatus,
} from "./omv";

vi.mock("./account-security", () => ({
  beginAdministratorTotpEnrollment: vi.fn(),
  confirmAdministratorTotpEnrollment: vi.fn(),
  disableAdministratorTotp: vi.fn(),
  fetchAdministratorTotpStatus: vi.fn(),
  revokeAllSessions: vi.fn(),
  rotateAdminPassword: vi.fn(),
}));

vi.mock("./approval", () => ({
  requestHighRiskApproval: vi.fn(),
}));

vi.mock("./omv", () => ({
  fetchNativeFilesystems: vi.fn(),
  fetchNativeHealth: vi.fn(),
  fetchNativeSmart: vi.fn(),
  fetchNativeSmartDevices: vi.fn(),
  fetchNativeStorageTopology: vi.fn(),
  fetchNativeStatus: vi.fn(),
  fetchOmvFilesystems: vi.fn(),
  fetchOmvHealth: vi.fn(),
  fetchOmvSharePrivileges: vi.fn(),
  fetchOmvSharingOverview: vi.fn(),
  fetchOmvSmart: vi.fn(),
  fetchOmvSmartDevices: vi.fn(),
  fetchOmvStorageTopology: vi.fn(),
  fetchOmvStatus: vi.fn(),
}));

vi.mock(
  "@/components/workspace/settings/system-agent-settings-content",
  () => ({
    OS_AGENT_SETTINGS_ITEMS: [
      { id: "models", label: "模型与 Codex" },
      { id: "tools", label: "工具、技能与 MCP" },
    ],
    SystemAgentSettingsContent: ({ section }: { section: string }) => (
      <div data-testid="embedded-agent-settings">{section}</div>
    ),
  }),
);

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
  vi.mocked(fetchAdministratorTotpStatus).mockResolvedValue({
    enabled: false,
    recoveryCodesRemaining: 0,
  });
  vi.mocked(beginAdministratorTotpEnrollment).mockResolvedValue({
    enrollmentId: "enrollment-id",
    secret: "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
    otpauthUri: "otpauth://totp/Echo%20OS%3Aadmin?secret=example",
    recoveryCodes: ["AAAA-BBBB-CCCC-DDDD"],
    expiresIn: 300,
  });
  vi.mocked(confirmAdministratorTotpEnrollment).mockResolvedValue({
    success: true,
    sessionsRevoked: true,
    sessionNotBefore: 44,
  });
  vi.mocked(disableAdministratorTotp).mockResolvedValue({
    success: true,
    sessionsRevoked: true,
    sessionNotBefore: 45,
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
  vi.mocked(fetchNativeStatus).mockResolvedValue({
    configured: true,
    available: true,
    readOnly: true,
    adminUrl: null,
    capabilities: [],
    source: "native",
  });
  vi.mocked(fetchNativeFilesystems).mockResolvedValue([]);
  vi.mocked(fetchNativeSmartDevices).mockResolvedValue([]);
  vi.mocked(fetchNativeStorageTopology).mockResolvedValue({
    devices: [],
    arrays: [],
  });
  vi.mocked(fetchNativeHealth).mockResolvedValue({
    schemaVersion: 1,
    state: "unknown",
    stale: false,
    checkedAt: null,
    lastSuccessfulAt: null,
    intervalSeconds: 0,
    persistenceHealthy: null,
    monitoring: false,
    activeAlerts: [],
    events: [],
    summary: { critical: 0, warning: 0, total: 0 },
    readOnly: true,
    coverage: "none",
    probeEvidence: [],
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

  it("enrolls administrator TOTP and shows one-time recovery material", async () => {
    const user = userEvent.setup();
    const onSessionEnded = vi.fn();
    render(
      <AccountSecurityPanel
        open
        onClose={vi.fn()}
        onSessionEnded={onSessionEnded}
      />,
    );

    await user.type(
      await screen.findByLabelText("启用动态验证码的管理员密码"),
      "current-device-pass",
    );
    await user.click(screen.getByRole("button", { name: "设置动态验证码…" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "credentials.totp.enroll",
        "admin",
        "current-device-pass",
      ),
    );
    expect(await screen.findByText("AAAA-BBBB-CCCC-DDDD")).toBeInTheDocument();
    await user.type(screen.getByLabelText("6 位动态验证码"), "123456");
    await user.click(screen.getByRole("button", { name: "启用并退出旧会话" }));

    await waitFor(() =>
      expect(confirmAdministratorTotpEnrollment).toHaveBeenCalledWith(
        "enrollment-id",
        "123456",
      ),
    );
    expect(onSessionEnded).toHaveBeenCalledWith(
      "动态验证码已启用，请使用密码和验证码重新登录",
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
    expect(fetchNativeStatus).toHaveBeenCalledOnce();
  });

  it("opens model configuration independently from Agent settings", async () => {
    const user = userEvent.setup();
    render(
      <AccountSecurityPanel open onClose={vi.fn()} onSessionEnded={vi.fn()} />,
    );

    await user.click(screen.getByRole("button", { name: "模型与用量" }));
    expect(
      screen.getByRole("heading", { name: "模型与用量" }),
    ).toBeInTheDocument();

    expect(screen.getByTestId("embedded-agent-settings")).toHaveTextContent(
      "models",
    );
    await user.click(screen.getByRole("button", { name: "AI 与 Agent" }));
    expect(
      screen.queryByRole("button", { name: "模型与 Codex" }),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("embedded-agent-settings")).toHaveTextContent(
      "tools",
    );
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
    expect(fetchNativeStatus).toHaveBeenCalled();
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
