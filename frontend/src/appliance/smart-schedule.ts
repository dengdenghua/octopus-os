import { approvalHeader } from "@/appliance/approval";
import { authHeader } from "@/appliance/auth";

export type SmartScheduleDesired = {
  schema: "echo.smart-self-test-schedule-desired.v1";
  enabled: boolean;
};

export type SmartScheduleStatus = {
  schemaVersion: 1;
  enabled: boolean;
  configured: boolean;
  source: "localPolicy";
  test: "short";
  schedule: string;
};

export type SmartSchedulePlan = {
  schema: "echo.smart-self-test-schedule-desired.v1";
  planId: string;
  operation: "none" | "enable" | "disable";
  requiresApproval: boolean;
  configured: boolean;
  current: { schemaVersion: 1; enabled: boolean };
  desired: { schemaVersion: 1; enabled: boolean };
  test: "short";
  schedule: string;
  applied?: boolean;
  verified?: boolean;
};

async function requestJson<T>(
  url: string,
  fallback: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      ...authHeader(),
      ...(init?.headers ?? {}),
    },
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

export function fetchSmartSchedule(): Promise<SmartScheduleStatus> {
  return requestJson(
    "/api/appliance/omv/smart/self-test/schedule",
    "无法读取 SMART 定时自检策略",
  );
}

export function planSmartSchedule(
  desired: SmartScheduleDesired,
): Promise<SmartSchedulePlan> {
  return requestJson(
    "/api/appliance/omv/smart/self-test/schedule/plan",
    "无法预览 SMART 定时自检策略",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(desired),
    },
  );
}

export function applySmartSchedule(
  desired: SmartScheduleDesired,
  planId: string,
  approvalToken: string,
): Promise<SmartSchedulePlan> {
  return requestJson(
    "/api/appliance/omv/smart/self-test/schedule/apply",
    "无法更新 SMART 定时自检策略",
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
