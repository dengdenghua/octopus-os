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

export class ApplianceAuthStatusError extends Error {
  constructor(
    message: string,
    public readonly status: number | null,
  ) {
    super(message);
    this.name = "ApplianceAuthStatusError";
  }
}

export class ApplianceSecondFactorRequiredError extends Error {
  constructor() {
    super("请输入动态验证码或恢复码");
    this.name = "ApplianceSecondFactorRequiredError";
  }
}

function isApplianceAuthStatus(value: unknown): value is ApplianceAuthStatus {
  if (!value || typeof value !== "object") return false;
  const status = value as Record<string, unknown>;
  return (
    typeof status.authRequired === "boolean" &&
    typeof status.authenticated === "boolean" &&
    (status.role === "operator" ||
      status.role === "member" ||
      status.role === null)
  );
}

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
  if (!response.ok) {
    throw new ApplianceAuthStatusError(
      `auth status failed: ${response.status}`,
      response.status,
    );
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!isApplianceAuthStatus(payload)) {
    throw new ApplianceAuthStatusError("auth status payload is invalid", 200);
  }
  return payload;
}

/** 登录后只依赖 HttpOnly Cookie，清掉可被 JS 读取的旧 JWT。 */
export async function applianceLogin(
  username: string,
  password: string,
  secondFactor?: string,
): Promise<void> {
  const normalizedUsername = username.trim();
  if (!normalizedUsername) throw new Error("请输入用户名");
  const response = await fetch("/api/auth/local/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username: normalizedUsername,
      password,
      ...(secondFactor ? { secondFactor } : {}),
    }),
  });
  if (!response.ok) {
    const detail: unknown = await response
      .json()
      .then((b) => b?.detail)
      .catch(() => null);
    if (response.status === 428 && detail === "second_factor_required") {
      throw new ApplianceSecondFactorRequiredError();
    }
    if (response.status === 429) {
      const retryAfter = response.headers.get("Retry-After");
      throw new Error(
        retryAfter
          ? `验证失败次数过多，请在 ${retryAfter} 秒后重试`
          : "验证失败次数过多，请稍后重试",
      );
    }
    throw new Error(typeof detail === "string" ? detail : "登录失败");
  }
  const data = (await response.json()) as { success?: boolean };
  if (!data.success) throw new Error("服务端未建立会话");
  _clearTokens();
}
