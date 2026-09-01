import { approvalHeader } from "@/appliance/approval";
import { authHeader } from "@/appliance/auth";

export type DeviceSyncScope = "photos" | "files";

export type DeviceSyncSummary = {
  committed: number;
  uploading: number;
  conflicts: number;
  bytes: number;
};

export type DeviceSyncDevice = {
  id: string;
  name: string;
  online: boolean;
  grants: Record<DeviceSyncScope, boolean>;
  summary: Record<DeviceSyncScope, DeviceSyncSummary>;
};

export type DeviceSyncStatus = {
  schema: "echo.device-sync.v1";
  available: boolean;
  mode: "echo-managed" | "agent-shared";
  conflictPolicy: "keep-both";
  roots: Record<DeviceSyncScope, string>;
  devices: DeviceSyncDevice[];
};

async function responseError(response: Response, fallback: string) {
  const detail = await response
    .json()
    .then((body) => body?.detail)
    .catch(() => null);
  if (response.status === 401) return new Error("登录已失效，请重新登录");
  return new Error(typeof detail === "string" ? detail : fallback);
}

export async function fetchDeviceSyncStatus(): Promise<DeviceSyncStatus> {
  const response = await fetch("/api/appliance/sync", {
    headers: authHeader(),
  });
  if (!response.ok) throw await responseError(response, "无法读取自动备份状态");
  return (await response.json()) as DeviceSyncStatus;
}

export async function setDeviceSyncScope(
  deviceId: string,
  scope: DeviceSyncScope,
  enabled: boolean,
  approvalToken: string,
): Promise<DeviceSyncStatus> {
  const operation = enabled ? "enable" : "disable";
  const response = await fetch(
    `/api/appliance/sync/devices/${encodeURIComponent(deviceId)}/${scope}/${operation}`,
    {
      method: "POST",
      headers: { ...authHeader(), ...approvalHeader(approvalToken) },
    },
  );
  if (!response.ok) throw await responseError(response, "自动备份设置失败");
  return (await response.json()) as DeviceSyncStatus;
}
