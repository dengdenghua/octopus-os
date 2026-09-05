import { approvalHeader } from "@/appliance/approval";
import { authHeader } from "@/appliance/auth";

export type NutUsbDriver =
  | "usbhid-ups"
  | "blazer_usb"
  | "nutdrv_qx"
  | "bcmxcp_usb"
  | "richcomm_usb"
  | "tripplite_usb";

export type NutDeviceDesired = {
  schema: "echo.nut-local-ups-desired.v1";
  enabled: boolean;
  driver: NutUsbDriver;
};

export type NutDeviceConfigStatus = {
  schemaVersion: 1;
  configured: boolean;
  enabled: boolean;
  name: "echo-ups";
  driver: NutUsbDriver | null;
  port: "auto";
  localOnly: boolean;
  externallyManaged: boolean;
  externalDeviceCount: number;
  allowedDrivers: NutUsbDriver[];
  shutdownOwner: "echo-ups-shutdown-guard";
};

export type NutDeviceConfigPlan = {
  schema: "echo.nut-local-ups-desired.v1";
  planId: string;
  operation: "none" | "enable" | "disable";
  requiresApproval: boolean;
  baseRevision: string;
  current: { enabled: boolean; driver: NutUsbDriver | null };
  desired: NutDeviceDesired;
  safety: {
    device: "singleLocalUsbUps";
    port: "auto";
    server: "loopbackOnly";
    upsmon: "notConfigured";
    shutdownOwner: "echo-ups-shutdown-guard";
  };
  applied?: boolean;
  verified?: boolean;
  config?: NutDeviceConfigStatus;
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
      throw new Error(detail || "配置已变化，请重新预览");
    throw new Error(detail || fallback);
  }
  return (await response.json()) as T;
}

export function fetchNutDeviceConfig(): Promise<NutDeviceConfigStatus> {
  return requestJson(
    "/api/appliance/omv/power/ups/config",
    "无法读取本机 UPS 配置",
  );
}

export function planNutDeviceConfig(
  desired: NutDeviceDesired,
): Promise<NutDeviceConfigPlan> {
  return requestJson(
    "/api/appliance/omv/power/ups/config/plan",
    "无法预览本机 UPS 配置",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(desired),
    },
  );
}

export function applyNutDeviceConfig(
  desired: NutDeviceDesired,
  planId: string,
  approvalToken: string,
): Promise<NutDeviceConfigPlan> {
  return requestJson(
    "/api/appliance/omv/power/ups/config/apply",
    "无法更新本机 UPS 配置",
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
