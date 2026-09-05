import { approvalHeader } from "@/appliance/approval";
import { authHeader } from "@/appliance/auth";

export type Ext4CheckFilesystem = {
  filesystemUuid: string;
  name: string;
  mountpoint: string;
  devicefile: string;
  array: {
    name: string;
    devicefile: string;
    uuid: string;
    level: "raid1";
    devices: string[];
    filesystem: null;
  };
  fstabSha256: string;
  mounted: boolean;
  stateHash: string;
  canCheck: boolean;
  reason: "mounted" | null;
};

export type Ext4CheckDesired = {
  schema: "echo.omv.ext4-check-desired.v1";
  filesystemUuid: string;
  operation: "check";
};

export type Ext4CheckPlan = {
  schema: "echo.omv.ext4-check-plan.v1";
  planId: string;
  operation: "offlineReadOnlyCheck";
  desired: Ext4CheckDesired;
  filesystem: Ext4CheckFilesystem;
  mountUnit: string;
  mountUnitState: "disabled" | "enabled" | "generated" | "indirect" | "static";
  requiresApproval: true;
  safety: {
    filesystem: "echoManagedExt4OnHealthyMdRaid1Only";
    mountedFilesystem: "rejected";
    automaticUnmount: false;
    automaticMount: false;
    repair: false;
    command: "e2fsckForcedReadOnly";
    mountUnit: "runtimeMaskedDuringCheck";
    ioLoad: "high";
  };
  applied?: boolean;
  verified?: boolean;
  clean?: boolean;
  errorsDetected?: boolean;
  result?: "clean" | "errorsFound";
  exitCode?: 0 | 4;
};

async function requestJson<T>(
  url: string,
  fallback: string,
  init?: RequestInit,
) {
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
      throw new Error(detail || "EXT4 状态已变化，请重新预览");
    throw new Error(detail || fallback);
  }
  return (await response.json()) as T;
}

export async function fetchExt4Checks(): Promise<Ext4CheckFilesystem[]> {
  const result = await requestJson<{ filesystems: Ext4CheckFilesystem[] }>(
    "/api/appliance/omv/volumes/ext4/checks",
    "无法读取 EXT4 离线检查状态",
  );
  return result.filesystems;
}

export function planExt4Check(
  desired: Ext4CheckDesired,
): Promise<Ext4CheckPlan> {
  return requestJson(
    "/api/appliance/omv/volumes/ext4/check/plan",
    "无法生成 EXT4 检查预览",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(desired),
    },
  );
}

export function applyExt4Check(
  desired: Ext4CheckDesired,
  planId: string,
  approvalToken: string,
): Promise<Ext4CheckPlan> {
  return requestJson(
    "/api/appliance/omv/volumes/ext4/check/apply",
    "无法执行 EXT4 离线检查",
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
