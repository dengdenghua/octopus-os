import { approvalHeader } from "@/appliance/approval";
import { authHeader } from "@/appliance/auth";

export type LinkedDevice = {
  id: string;
  type: string;
  platform: string;
  brand: string;
  model: string;
  version: string;
  status: string;
  online: boolean;
  busy: boolean;
  battery: number | null;
  charging: boolean;
  currentApp: string;
  pairedAt: number | null;
  lastSeenAt: number | null;
  capabilities: string[];
  totalCapabilities: number;
  individuallyRevocable: boolean;
};

export type DeviceLinkStatus = {
  schema: "echo.device-link.v1";
  enabled: boolean;
  listenerActive: boolean;
  mode: "echo-managed" | "agent-shared";
  scope: "lan";
  wsPort: number;
  canManageListener: boolean;
  canPair: boolean;
  pairedDeviceCount: number;
  onlineDeviceCount: number;
  devices: LinkedDevice[];
  startupError: string;
  transport: {
    protocol: "websocket";
    encrypted: boolean;
    authenticated: boolean;
  };
  remoteAccess: {
    schema: "echo.remote-access.v1";
    provider: "none" | "tailscale" | string;
    available: boolean;
    mode: "not-configured" | string;
    configured: boolean;
    state: "not-configured" | "connecting" | "connected" | string;
    scope: "none" | "private-network" | string;
    endpoint: string | null;
    lastCheckedAt: number | null;
    transport: {
      protocol: "none" | "wireguard+https" | string;
      encrypted: boolean;
      tailnetOnly: boolean;
    };
    features: {
      desktopWeb: boolean;
      deviceLink: boolean;
      fileSync: boolean;
      photoSync: boolean;
    };
    reason: string;
  };
};

export type PairingInvitation = {
  schema: "echo.device-link.invitation.v1";
  scope: "lan";
  wsUrl: string;
  connectString: string;
  expiresAt: number | null;
  credentialMode: "per-device" | "shared";
  deviceSync?: {
    baseUrl: string;
    protocolVersion: number;
    transport: "lan-http" | "tailnet-https";
  };
};

async function responseError(response: Response, fallback: string) {
  const detail = await response
    .json()
    .then((body) => body?.detail)
    .catch(() => null);
  if (response.status === 401) return new Error("登录已失效，请重新登录");
  if (response.status === 409)
    return new Error(detail || "当前状态不允许此操作");
  return new Error(detail || fallback);
}

export async function fetchDeviceLinkStatus(): Promise<DeviceLinkStatus> {
  const response = await fetch("/api/appliance/device-link", {
    headers: authHeader(),
  });
  if (!response.ok) throw await responseError(response, "无法读取设备连接状态");
  return (await response.json()) as DeviceLinkStatus;
}

async function approvedRequest(
  path: string,
  method: "POST" | "DELETE",
  approvalToken: string,
): Promise<DeviceLinkStatus> {
  const response = await fetch(path, {
    method,
    headers: { ...authHeader(), ...approvalHeader(approvalToken) },
  });
  if (!response.ok) throw await responseError(response, "设备连接操作失败");
  return (await response.json()) as DeviceLinkStatus;
}

export function enableDeviceLink(approvalToken: string) {
  return approvedRequest(
    "/api/appliance/device-link/enable",
    "POST",
    approvalToken,
  );
}

export function disableDeviceLink(approvalToken: string) {
  return approvedRequest(
    "/api/appliance/device-link/disable",
    "POST",
    approvalToken,
  );
}

export async function createPairingInvitation(
  approvalToken: string,
): Promise<PairingInvitation> {
  const response = await fetch(
    "/api/appliance/device-link/pairing-invitations",
    {
      method: "POST",
      headers: { ...authHeader(), ...approvalHeader(approvalToken) },
    },
  );
  if (!response.ok) throw await responseError(response, "无法创建配对邀请");
  return (await response.json()) as PairingInvitation;
}

export function revokeLinkedDevice(deviceId: string, approvalToken: string) {
  return approvedRequest(
    `/api/appliance/device-link/devices/${encodeURIComponent(deviceId)}`,
    "DELETE",
    approvalToken,
  );
}
