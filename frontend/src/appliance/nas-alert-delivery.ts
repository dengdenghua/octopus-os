import { approvalHeader, type HighRiskAction } from "@/appliance/approval";
import { authHeader } from "@/appliance/auth";

export type NasAlertDeliveryStatus = {
  schema: "echo.nas-alert-delivery.v1";
  configured: boolean;
  enabled: boolean;
  destinationHost: string | null;
  hasBearerToken: boolean;
  deliveredActiveAlerts: number;
  consecutiveFailures: number;
  nextRetryAt: string | null;
  lastAttemptAt: string | null;
  lastSuccessAt: string | null;
  lastError: "delivery_failed" | "health_probe_failed" | null;
  persistenceHealthy: boolean;
  monitoring: boolean;
  revision: string;
  secretsRedacted: true;
};

export type NasAlertDeliveryPlan = {
  schema: "echo.nas-alert-delivery.v1";
  planId: string;
  changes: Array<{ field: string; before: unknown; after: unknown }>;
  requiresApproval: true;
  secretsPersistedEncrypted: true;
  redirectsAllowed: false;
  publicHttpsOnly: true;
};

async function responseError(
  response: Response,
  fallback: string,
): Promise<Error> {
  const detail = await response
    .json()
    .then((body) => body?.detail)
    .catch(() => null);
  return new Error(detail || fallback);
}

export async function fetchNasAlertDeliveryStatus(): Promise<NasAlertDeliveryStatus> {
  const response = await fetch("/api/appliance/notifications/webhook", {
    headers: authHeader(),
  });
  if (!response.ok) throw await responseError(response, "无法读取外部告警状态");
  return (await response.json()) as NasAlertDeliveryStatus;
}

export async function planNasAlertDelivery(input: {
  enabled: boolean;
  url?: string;
  bearerToken?: string;
}): Promise<NasAlertDeliveryPlan> {
  const response = await fetch("/api/appliance/notifications/webhook/plan", {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok)
    throw await responseError(response, "无法生成外部告警配置预览");
  return (await response.json()) as NasAlertDeliveryPlan;
}

export async function applyNasAlertDelivery(
  planId: string,
  approvalToken: string,
): Promise<NasAlertDeliveryStatus> {
  const response = await fetch("/api/appliance/notifications/webhook/apply", {
    method: "POST",
    headers: {
      ...authHeader(),
      ...approvalHeader(approvalToken),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ planId }),
  });
  if (!response.ok) throw await responseError(response, "外部告警配置未应用");
  return (await response.json()) as NasAlertDeliveryStatus;
}

export async function testNasAlertDelivery(
  approvalToken: string,
): Promise<{ sent: true; sentAt: string }> {
  const response = await fetch("/api/appliance/notifications/webhook/test", {
    method: "POST",
    headers: { ...authHeader(), ...approvalHeader(approvalToken) },
  });
  if (!response.ok) throw await responseError(response, "测试通知发送失败");
  return (await response.json()) as { sent: true; sentAt: string };
}

export const NAS_ALERT_CONFIGURE_ACTION =
  "notifications.webhook.configure" satisfies HighRiskAction;
export const NAS_ALERT_TEST_ACTION =
  "notifications.webhook.test" satisfies HighRiskAction;
