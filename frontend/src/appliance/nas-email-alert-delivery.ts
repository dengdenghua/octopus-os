import { approvalHeader, type HighRiskAction } from "@/appliance/approval";
import { authHeader } from "@/appliance/auth";

export type NasEmailAlertDeliveryStatus = {
  schema: "echo.nas-alert-email.v1";
  configured: boolean;
  enabled: boolean;
  destinationHost: string | null;
  smtpPort: 465 | 587 | null;
  security: "implicit_tls" | "starttls" | null;
  recipientHint: string | null;
  hasCredentials: boolean;
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

export type NasEmailAlertDeliveryPlan = {
  schema: "echo.nas-alert-email.v1";
  planId: string;
  changes: Array<{ field: string; before: unknown; after: unknown }>;
  requiresApproval: true;
  secretsPersistedEncrypted: true;
  publicSmtpOnly: true;
  tlsRequired: true;
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

export async function fetchNasEmailAlertDeliveryStatus(): Promise<NasEmailAlertDeliveryStatus> {
  const response = await fetch("/api/appliance/notifications/email", {
    headers: authHeader(),
  });
  if (!response.ok) throw await responseError(response, "无法读取邮件告警状态");
  return (await response.json()) as NasEmailAlertDeliveryStatus;
}

export async function planNasEmailAlertDelivery(input: {
  enabled: boolean;
  smtpHost?: string;
  smtpPort?: 465 | 587;
  username?: string;
  password?: string;
  fromAddress?: string;
  recipient?: string;
}): Promise<NasEmailAlertDeliveryPlan> {
  const response = await fetch("/api/appliance/notifications/email/plan", {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok)
    throw await responseError(response, "无法生成邮件告警配置预览");
  return (await response.json()) as NasEmailAlertDeliveryPlan;
}

export async function applyNasEmailAlertDelivery(
  planId: string,
  approvalToken: string,
): Promise<NasEmailAlertDeliveryStatus> {
  const response = await fetch("/api/appliance/notifications/email/apply", {
    method: "POST",
    headers: {
      ...authHeader(),
      ...approvalHeader(approvalToken),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ planId }),
  });
  if (!response.ok) throw await responseError(response, "邮件告警配置未应用");
  return (await response.json()) as NasEmailAlertDeliveryStatus;
}

export async function testNasEmailAlertDelivery(
  approvalToken: string,
): Promise<{ sent: true; sentAt: string }> {
  const response = await fetch("/api/appliance/notifications/email/test", {
    method: "POST",
    headers: { ...authHeader(), ...approvalHeader(approvalToken) },
  });
  if (!response.ok) throw await responseError(response, "测试邮件发送失败");
  return (await response.json()) as { sent: true; sentAt: string };
}

export const NAS_EMAIL_ALERT_CONFIGURE_ACTION =
  "notifications.email.configure" satisfies HighRiskAction;
export const NAS_EMAIL_ALERT_TEST_ACTION =
  "notifications.email.test" satisfies HighRiskAction;
