import { approvalHeader } from "@/appliance/approval";
import { authHeader } from "@/appliance/auth";

type AccountSecurityResult = {
  success: boolean;
  sessionsRevoked: boolean;
  sessionNotBefore: number;
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
