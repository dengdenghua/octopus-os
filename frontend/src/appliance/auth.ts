/**
 * Octopus OS appliance 单用户认证(前端)。
 *
 * 登录复用 runtime 现成的 /api/auth/local/login(签发长会话 JWT);
 * token 写入与既有 AuthProvider 共享的存储(_writeToken),并由
 * authHeader() 附到 appliance 接口请求上。
 */

import { _writeToken, getToken } from "@/core/auth/api";

const ADMIN_USERNAME = "admin";

export function authHeader(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export type ApplianceAuthStatus = {
  authRequired: boolean;
  authenticated: boolean;
};

export async function fetchApplianceAuthStatus(): Promise<ApplianceAuthStatus> {
  const response = await fetch("/api/appliance/auth/status", {
    headers: authHeader(),
  });
  if (!response.ok) throw new Error(`auth status failed: ${response.status}`);
  return (await response.json()) as ApplianceAuthStatus;
}

/** 用管理员密码登录;成功后写入共享 token 存储,使后续请求带上 JWT。 */
export async function applianceLogin(password: string): Promise<void> {
  const response = await fetch("/api/auth/local/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: ADMIN_USERNAME, password }),
  });
  if (!response.ok) {
    const detail = await response
      .json()
      .then((b) => b?.detail)
      .catch(() => null);
    throw new Error(detail || "登录失败");
  }
  const data = (await response.json()) as {
    access_token?: string;
    actor_id?: string;
  };
  if (!data.access_token) throw new Error("服务端未启用认证");
  _writeToken(data.access_token, {
    user_id: data.actor_id ?? `local:${ADMIN_USERNAME}`,
    username: ADMIN_USERNAME,
    is_guest: false,
  });
}
