import { authHeader } from "@/appliance/auth";

export type HighRiskAction =
  | "app.start"
  | "app.stop"
  | "hub.app.install"
  | "hub.app.update"
  | "hub.app.uninstall"
  | "hub.app.start"
  | "hub.app.stop"
  | "hub.app.restart"
  | "agent.capability.install"
  | "agent.capability.authorize"
  | "agent.capability.uninstall"
  | "agent.capability.rollback"
  | "photos.index.build"
  | "files.trash.empty"
  | "sessions.revoke"
  | "credentials.rotate"
  | "audit.key.rotate"
  | "device-link.enable"
  | "device-link.disable"
  | "device-link.pair"
  | "device-link.device.revoke"
  | "device-sync.photos.enable"
  | "device-sync.photos.disable"
  | "device-sync.files.enable"
  | "device-sync.files.disable"
  | "omv.shared-folder.create"
  | "omv.share-privilege.apply"
  | "omv.smb.apply"
  | "omv.nfs.apply"
  | "omv.quota.apply"
  | "omv.group.create"
  | "omv.user.create"
  | "omv.user.password.reset"
  | "account.member.link"
  | "account.member.status.set"
  | "account.member.password.reset"
  | "account.member.unlink";

type ApprovalResponse = {
  approvalToken: string;
  expiresIn: number;
  action: HighRiskAction;
  target: string;
};

async function responseError(response: Response, fallback: string) {
  const detail = await response
    .json()
    .then((body) => body?.detail)
    .catch(() => null);
  if (response.status === 429) {
    const retryAfter = response.headers.get("Retry-After");
    return new Error(
      retryAfter
        ? `复核失败次数过多，请在 ${retryAfter} 秒后重试`
        : "复核失败次数过多，请稍后重试",
    );
  }
  if (response.status === 403) {
    return new Error("设备管理员密码不正确，操作未执行");
  }
  return new Error(detail || fallback);
}

/**
 * 用设备管理员密码签发短时、单次且与 action/target 绑定的审批令牌。
 * 密码只在本次 HTTPS 请求体中发送，绝不写入本地存储。
 */
export async function requestHighRiskApproval(
  action: HighRiskAction,
  target: string,
  password: string,
): Promise<ApprovalResponse> {
  const response = await fetch("/api/appliance/approvals", {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ action, target, password }),
  });
  if (!response.ok) {
    throw await responseError(response, "管理员复核失败");
  }
  return (await response.json()) as ApprovalResponse;
}

export function approvalHeader(token: string): Record<string, string> {
  return { "X-Echo-Approval": token };
}
