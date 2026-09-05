import { approvalHeader } from "@/appliance/approval";
import { authHeader } from "@/appliance/auth";

export type DiskIdleDevice = {
  devicefile: string;
  model: string | null;
  sizeBytes: number;
  transport: "ata" | "sata";
  identityHash: string;
};

export type DiskIdleDesired = {
  schema: "echo.disk-idle-policy-desired.v1";
  idleMinutes: 0 | 30 | 60 | 120 | 240;
};

export type DiskIdleStatus = {
  schemaVersion: 1;
  enabled: boolean;
  idleMinutes: DiskIdleDesired["idleMinutes"];
  configured: boolean;
  serviceInstalled: boolean;
  eligibleDevices: DiskIdleDevice[];
  eligibleDeviceCount: number;
  allowedIdleMinutes: DiskIdleDesired["idleMinutes"][];
  scope: "stableInternalRotationalAtaSataWholeDisksOnly";
  hardwareVerification: "commandAcceptanceOnly";
  source: "localPolicy";
};

export type DiskIdlePlan = {
  schema: "echo.disk-idle-policy-desired.v1";
  planId: string;
  operation: "none" | "set" | "disable";
  requiresApproval: boolean;
  current: { schemaVersion: 1; idleMinutes: DiskIdleDesired["idleMinutes"] };
  desired: { schemaVersion: 1; idleMinutes: DiskIdleDesired["idleMinutes"] };
  configured: boolean;
  serviceInstalled: boolean;
  devices: DiskIdleDevice[];
  scope: "stableInternalRotationalAtaSataWholeDisksOnly";
  hardwareVerification: "commandAcceptanceOnly";
  safety: {
    minimumIdleMinutes: 30;
    nvme: "skipped";
    usbAndRemovable: "skipped";
    unknownIdentity: "skipped";
    firmwareMayIgnoreTimer: true;
    activeIoPreventsStandby: true;
  };
  applied?: boolean;
  verified?: boolean;
  hardwareUpdated?: number;
};

async function requestJson<T>(
  url: string,
  fallback: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { ...authHeader(), ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const detail = await response
      .json()
      .then((value) => value?.detail)
      .catch(() => null);
    if (response.status === 401) throw new Error("登录已失效，请重新登录");
    if (response.status === 409)
      throw new Error(detail || "磁盘已变化，请重新预览");
    throw new Error(detail || fallback);
  }
  return (await response.json()) as T;
}

export function fetchDiskIdle(): Promise<DiskIdleStatus> {
  return requestJson("/api/appliance/omv/disks/idle", "无法读取磁盘休眠策略");
}

export function planDiskIdle(desired: DiskIdleDesired): Promise<DiskIdlePlan> {
  return requestJson(
    "/api/appliance/omv/disks/idle/plan",
    "无法预览磁盘休眠策略",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(desired),
    },
  );
}

export function applyDiskIdle(
  desired: DiskIdleDesired,
  planId: string,
  approvalToken: string,
): Promise<DiskIdlePlan> {
  return requestJson(
    "/api/appliance/omv/disks/idle/apply",
    "无法更新磁盘休眠策略",
    {
      method: "POST",
      headers: {
        ...approvalHeader(approvalToken),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ desired, planId }),
    },
  );
}
