import { approvalHeader } from "@/appliance/approval";
import { authHeader } from "@/appliance/auth";

export type MdRaidMaintenance = {
  array: { name: string; devicefile: string; uuid: string };
  kernelDevice: string;
  members: Array<{ devicefile: string; slot: number | null; states: string[] }>;
  healthy: boolean;
  degradedDevices: number;
  action:
    | "idle"
    | "check"
    | "repair"
    | "recover"
    | "resync"
    | "reshape"
    | "frozen";
  progressPercent: number | null;
  mismatchCount: number;
  stateHash: string;
  canStartCheck: boolean;
};

export type MdRaidCheckDesired = {
  schema: "echo.omv.mdraid-check-desired.v1";
  name: string;
  arrayUuid: string;
  operation: "start";
};

export type MdRaidCheckPlan = {
  schema: "echo.omv.mdraid-check-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "start";
  requiresApproval: true;
  desired: MdRaidCheckDesired;
  array: MdRaidMaintenance["array"];
  before: Pick<
    MdRaidMaintenance,
    | "healthy"
    | "degradedDevices"
    | "action"
    | "progressPercent"
    | "mismatchCount"
    | "stateHash"
  >;
  applied?: boolean;
  verified?: boolean;
  maintenanceState?: "checking" | "completed";
  current?: MdRaidMaintenance;
};

export type MdRaidCheckSchedule = {
  schemaVersion: 1;
  enabled: boolean;
  configured: boolean;
  schedulerInstalled: boolean;
  source: "localPolicy";
  operation: "check";
  scope: "echoManagedHealthyRaid1Only";
  schedule: string;
};

export type MdRaidCheckScheduleDesired = {
  schema: "echo.mdraid-check-schedule-desired.v1";
  enabled: boolean;
};

export type MdRaidCheckSchedulePlan = {
  schema: "echo.mdraid-check-schedule-desired.v1";
  planId: string;
  current: { schemaVersion: 1; enabled: boolean };
  desired: { schemaVersion: 1; enabled: boolean };
  configured: boolean;
  schedulerInstalled: boolean;
  operation: "none" | "enable" | "disable";
  checkAction: "check";
  scope: "echoManagedHealthyRaid1Only";
  schedule: string;
  requiresApproval: boolean;
  applied?: boolean;
  verified?: boolean;
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
      throw new Error(detail || "阵列状态已变化，请重新预览");
    throw new Error(detail || fallback);
  }
  return (await response.json()) as T;
}

function postJson<T>(
  url: string,
  body: unknown,
  fallback: string,
  headers?: Record<string, string>,
) {
  return requestJson<T>(url, fallback, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

export async function fetchMdRaidMaintenance(): Promise<MdRaidMaintenance[]> {
  const result = await requestJson<{ arrays: MdRaidMaintenance[] }>(
    "/api/appliance/omv/arrays/mdraid1/maintenance",
    "无法读取 RAID1 校验状态",
  );
  return result.arrays;
}

export function planMdRaidCheck(
  desired: MdRaidCheckDesired,
): Promise<MdRaidCheckPlan> {
  return postJson<MdRaidCheckPlan>(
    "/api/appliance/omv/arrays/mdraid1/check/plan",
    desired,
    "无法生成 RAID1 一致性校验预览",
  );
}

export function applyMdRaidCheck(
  desired: MdRaidCheckDesired,
  planId: string,
  approvalToken: string,
): Promise<MdRaidCheckPlan> {
  return postJson<MdRaidCheckPlan>(
    "/api/appliance/omv/arrays/mdraid1/check/apply",
    { desired, planId },
    "无法启动 RAID1 一致性校验",
    approvalHeader(approvalToken),
  );
}

export function fetchMdRaidCheckSchedule(): Promise<MdRaidCheckSchedule> {
  return requestJson<MdRaidCheckSchedule>(
    "/api/appliance/omv/arrays/mdraid1/check/schedule",
    "无法读取 RAID1 月度校验策略",
  );
}

export function planMdRaidCheckSchedule(
  desired: MdRaidCheckScheduleDesired,
): Promise<MdRaidCheckSchedulePlan> {
  return postJson<MdRaidCheckSchedulePlan>(
    "/api/appliance/omv/arrays/mdraid1/check/schedule/plan",
    desired,
    "无法生成 RAID1 月度校验策略预览",
  );
}

export function applyMdRaidCheckSchedule(
  desired: MdRaidCheckScheduleDesired,
  planId: string,
  approvalToken: string,
): Promise<MdRaidCheckSchedulePlan> {
  return postJson<MdRaidCheckSchedulePlan>(
    "/api/appliance/omv/arrays/mdraid1/check/schedule/apply",
    { desired, planId },
    "无法更新 RAID1 月度校验策略",
    approvalHeader(approvalToken),
  );
}
