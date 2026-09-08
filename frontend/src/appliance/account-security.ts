import { approvalHeader } from "@/appliance/approval";
import { authHeader } from "@/appliance/auth";

type AccountSecurityResult = {
  success: boolean;
  sessionsRevoked: boolean;
  sessionNotBefore: number;
};

export type AdministratorTotpStatus = {
  enabled: boolean;
  recoveryCodesRemaining: number;
};

export type AdministratorTotpEnrollment = {
  enrollmentId: string;
  secret: string;
  otpauthUri: string;
  recoveryCodes: string[];
  expiresIn: number;
};

async function responseError(response: Response, fallback: string) {
  const detail = await response
    .json()
    .then((body) => body?.detail)
    .catch(() => null);
  if (response.status === 401) return new Error("登录已失效，请重新登录");
  if (response.status === 422) return new Error(detail || "密码不符合安全要求");
  return new Error(detail || fallback);
}

export async function revokeAllSessions(
  approvalToken: string,
): Promise<AccountSecurityResult> {
  const response = await fetch("/api/appliance/sessions/revoke", {
    method: "POST",
    headers: { ...authHeader(), ...approvalHeader(approvalToken) },
  });
  if (!response.ok) throw await responseError(response, "无法退出全部登录");
  return (await response.json()) as AccountSecurityResult;
}

export async function rotateAdminPassword(
  newPassword: string,
  approvalToken: string,
): Promise<AccountSecurityResult> {
  const response = await fetch("/api/appliance/credentials/rotate", {
    method: "POST",
    headers: {
      ...authHeader(),
      ...approvalHeader(approvalToken),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ newPassword }),
  });
  if (!response.ok) throw await responseError(response, "无法更新管理员密码");
  return (await response.json()) as AccountSecurityResult;
}

export async function fetchAdministratorTotpStatus(): Promise<AdministratorTotpStatus> {
  const response = await fetch("/api/appliance/credentials/totp", {
    headers: authHeader(),
  });
  if (!response.ok)
    throw await responseError(response, "无法读取动态验证码状态");
  return (await response.json()) as AdministratorTotpStatus;
}

export async function beginAdministratorTotpEnrollment(
  approvalToken: string,
): Promise<AdministratorTotpEnrollment> {
  const response = await fetch("/api/appliance/credentials/totp/enroll", {
    method: "POST",
    headers: { ...authHeader(), ...approvalHeader(approvalToken) },
  });
  if (!response.ok)
    throw await responseError(response, "无法开始设置动态验证码");
  return (await response.json()) as AdministratorTotpEnrollment;
}

export async function confirmAdministratorTotpEnrollment(
  enrollmentId: string,
  code: string,
): Promise<AccountSecurityResult> {
  const response = await fetch("/api/appliance/credentials/totp/confirm", {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ enrollmentId, code }),
  });
  if (!response.ok) throw await responseError(response, "无法启用动态验证码");
  return (await response.json()) as AccountSecurityResult;
}

export async function disableAdministratorTotp(
  factor: string,
  approvalToken: string,
): Promise<AccountSecurityResult> {
  const response = await fetch("/api/appliance/credentials/totp/disable", {
    method: "POST",
    headers: {
      ...authHeader(),
      ...approvalHeader(approvalToken),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ factor }),
  });
  if (!response.ok) throw await responseError(response, "无法关闭动态验证码");
  return (await response.json()) as AccountSecurityResult;
}
