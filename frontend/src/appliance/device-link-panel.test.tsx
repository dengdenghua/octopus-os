import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestHighRiskApproval } from "./approval";
import { createPairingInvitation, fetchDeviceLinkStatus } from "./device-link";
import { DeviceLinkPanel } from "./device-link-panel";
import { fetchDeviceSyncStatus, setDeviceSyncScope } from "./device-sync";

vi.mock("./approval", () => ({ requestHighRiskApproval: vi.fn() }));
vi.mock("./device-link", () => ({
  fetchDeviceLinkStatus: vi.fn(),
  enableDeviceLink: vi.fn(),
  disableDeviceLink: vi.fn(),
  createPairingInvitation: vi.fn(),
  revokeLinkedDevice: vi.fn(),
}));
vi.mock("./device-sync", () => ({
  fetchDeviceSyncStatus: vi.fn(),
  setDeviceSyncScope: vi.fn(),
}));

const status = {
  schema: "echo.device-link.v1" as const,
  enabled: true,
  listenerActive: true,
  mode: "echo-managed" as const,
  scope: "lan" as const,
  wsPort: 8765,
  canManageListener: true,
  canPair: true,
  pairedDeviceCount: 1,
  onlineDeviceCount: 1,
  devices: [
    {
      id: "phone-1",
      type: "mobile",
      platform: "android",
      brand: "Echo",
      model: "Pocket",
      version: "1.0",
      status: "online",
      online: true,
      busy: false,
      battery: 82,
      charging: false,
      currentApp: "相册",
      pairedAt: 1,
      lastSeenAt: 1,
      capabilities: ["android.tap"],
      totalCapabilities: 8,
      individuallyRevocable: true,
    },
  ],
  startupError: "",
  transport: {
    protocol: "websocket" as const,
    encrypted: false,
    authenticated: true,
  },
  remoteAccess: {
    schema: "echo.remote-access.v1" as const,
    provider: "none",
    available: false,
    mode: "not-configured",
    configured: false,
    state: "not-configured",
    scope: "none",
    endpoint: null,
    lastCheckedAt: null,
    transport: {
      protocol: "none",
      encrypted: false,
      tailnetOnly: false,
    },
    features: {
      desktopWeb: false,
      deviceLink: false,
      fileSync: false,
      photoSync: false,
    },
    reason: "private network or relay is not configured",
  },
};

const syncStatus = {
  schema: "echo.device-sync.v1" as const,
  available: true,
  mode: "echo-managed" as const,
  conflictPolicy: "keep-both" as const,
  roots: {
    photos: "Mobile Uploads/<device>/Photos",
    files: "Mobile Uploads/<device>/Files",
  },
  devices: [
    {
      id: "phone-1",
      name: "Echo Pocket",
      online: true,
      grants: { photos: false, files: false },
      summary: {
        photos: { committed: 0, uploading: 0, conflicts: 0, bytes: 0 },
        files: { committed: 0, uploading: 0, conflicts: 0, bytes: 0 },
      },
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchDeviceLinkStatus).mockResolvedValue(status);
  vi.mocked(fetchDeviceSyncStatus).mockResolvedValue(syncStatus);
  vi.mocked(setDeviceSyncScope).mockResolvedValue({
    ...syncStatus,
    devices: [
      {
        ...syncStatus.devices[0],
        grants: { photos: true, files: false },
      },
    ],
  });
  vi.mocked(requestHighRiskApproval).mockResolvedValue({
    approvalToken: "approved",
    expiresIn: 90,
    action: "device-link.pair",
    target: "lan",
  });
  vi.mocked(createPairingInvitation).mockResolvedValue({
    schema: "echo.device-link.invitation.v1",
    scope: "lan",
    wsUrl: "ws://192.168.1.2:8765",
    connectString: "echo://join?ws=local&token=secret",
    expiresAt: 2_000,
    credentialMode: "per-device",
    deviceSync: {
      baseUrl: "http://192.168.1.2:8000",
      protocolVersion: 1,
      transport: "lan-http",
    },
  });
});

describe("device link panel", () => {
  it("shows real Tentacle devices and the honest remote-access boundary", async () => {
    const user = userEvent.setup();
    render(<DeviceLinkPanel open onClose={vi.fn()} />);

    expect(await screen.findByText("Echo Pocket")).toBeInTheDocument();
    expect(screen.getByText("8 项设备能力")).toBeInTheDocument();
    expect(screen.getByText("1 台已配对 · 1 台在线")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "远程访问" }));
    expect(screen.getByText("当前只支持同一局域网")).toBeInTheDocument();
    expect(screen.getByText(/Tailscale 私网/)).toBeInTheDocument();
    expect(screen.getByText(/局域网未加密/)).toBeInTheDocument();
  });

  it("requires password approval before revealing a pairing link", async () => {
    const user = userEvent.setup();
    render(<DeviceLinkPanel open onClose={vi.fn()} />);

    await screen.findByText("Echo Pocket");
    await user.click(screen.getByRole("button", { name: "添加设备" }));
    await user.click(screen.getByRole("button", { name: "创建配对邀请…" }));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    await user.type(screen.getByLabelText("设备管理员密码"), "password");
    await user.click(screen.getByRole("button", { name: "创建邀请" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "device-link.pair",
        "lan",
        "password",
      ),
    );
    expect(
      await screen.findByText("echo://join?ws=local&token=secret"),
    ).toBeInTheDocument();
    expect(screen.getByText(/自动备份入口已一并配置/)).toBeInTheDocument();
  });

  it("shows a connected Tailscale web gateway without claiming remote device control", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchDeviceLinkStatus).mockResolvedValue({
      ...status,
      remoteAccess: {
        schema: "echo.remote-access.v1",
        provider: "tailscale",
        available: true,
        mode: "sidecar",
        configured: true,
        state: "connected",
        scope: "private-network",
        endpoint: "https://echo-os.example.ts.net",
        lastCheckedAt: 2_000,
        transport: {
          protocol: "wireguard+https",
          encrypted: true,
          tailnetOnly: true,
        },
        features: {
          desktopWeb: true,
          deviceLink: false,
          fileSync: false,
          photoSync: false,
        },
        reason: "connected",
      },
    });
    render(<DeviceLinkPanel open onClose={vi.fn()} />);

    await screen.findByText("Echo Pocket");
    await user.click(screen.getByRole("button", { name: "远程访问" }));

    expect(screen.getByText("已可从个人私网访问 Echo")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /打开远程地址/ })).toHaveAttribute(
      "href",
      "https://echo-os.example.ts.net",
    );
    expect(
      screen.getByText(/Tentacle 控制端口仍只在局域网/),
    ).toBeInTheDocument();
    expect(screen.getByText(/WireGuard \+ HTTPS/)).toBeInTheDocument();
  });

  it("separates photo backup permission from pairing and requires approval", async () => {
    const user = userEvent.setup();
    render(<DeviceLinkPanel open onClose={vi.fn()} />);

    await screen.findByText("Echo Pocket");
    await user.click(screen.getByRole("button", { name: "自动备份" }));
    expect(screen.getByText("服务端同步已就绪")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "开启照片备份：Echo Pocket" }),
    );
    await user.type(screen.getByLabelText("设备管理员密码"), "password");
    await user.click(screen.getByRole("button", { name: "确认开启" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "device-sync.photos.enable",
        "phone-1",
        "password",
      ),
    );
    expect(setDeviceSyncScope).toHaveBeenCalledWith(
      "phone-1",
      "photos",
      true,
      "approved",
    );
  });
});
