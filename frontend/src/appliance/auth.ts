/**
 * Echo OS appliance 家庭成员认证(前端)。
 *
 * 登录复用 runtime 现成的 /api/auth/local/login。浏览器会话只保存在服务端
 * 设置的 HttpOnly Cookie 中，不把 JWT 复制进 localStorage；authHeader() 仅
 * 兼容既有非 appliance 会话和 CLI 风格开发调用。
 */

import { _clearTokens, getToken } from "@/core/auth/api";

export function authHeader(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export type ApplianceAuthStatus = {
  authRequired: boolean;
  authenticated: boolean;
  role: "operator" | "member" | null;
};

/**
 * A desktop without the appliance login gate is managed by its local user.
 * When the gate is enabled, only an authenticated operator may change the
 * device. Keeping this rule here prevents a null role in browser mode from
 * accidentally disabling every system setting.
 */
export function hasDeviceOperatorAccess(
  authRequired: boolean | null,
  authenticated: boolean | null,
  role: ApplianceAuthStatus["role"],
): boolean {
  return (
    authRequired === false ||
    (authRequired === true && authenticated === true && role === "operator")
  );
}

export async function fetchApplianceAuthStatus(): Promise<ApplianceAuthStatus> {
  const response = await fetch("/api/appliance/auth/status", {
    headers: authHeader(),
  });
  if (!response.ok) throw new Error(`auth status failed: ${response.status}`);
  return (await response.json()) as ApplianceAuthStatus;
}

/** 登录后只依赖 HttpOnly Cookie，清掉可被 JS 读取的旧 JWT。 */
export async function applianceLogin(
  username: string,
  password: string,
): Promise<void> {
  const normalizedUsername = username.trim();
  if (!normalizedUsername) throw new Error("请输入用户名");
  const response = await fetch("/api/auth/local/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: normalizedUsername, password }),
  });
  if (!response.ok) {
    const detail = await response
      .json()
      .then((b) => b?.detail)
      .catch(() => null);
    throw new Error(detail || "登录失败");
  }
  const data = (await response.json()) as { success?: boolean };
  if (!data.success) throw new Error("服务端未建立会话");
  _clearTokens();
}
