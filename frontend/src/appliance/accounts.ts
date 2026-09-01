import { approvalHeader } from "@/appliance/approval";
import { authHeader } from "@/appliance/auth";

export type EchoLocalAccount = {
  username: string;
  displayName: string;
  role: "admin" | "member";
  omvUsername: string | null;
  active: boolean;
};

export type EchoAccountDirectory = {
  schema: "echo.account-directory.v1";
  accounts: EchoLocalAccount[];
  canManage: boolean;
};

export type EchoAccountLinkDesired = {
  omvUsername: string;
  displayName: string;
  password: string;
};

export type EchoAccountLinkPlan = {
  schema: "echo.account-link-plan.v1";
  planId: string;
  operation: "linkExistingOmvMember";
  requiresApproval: true;
  account: Omit<EchoLocalAccount, "active">;
  changes: string[];
  safety: {
    omvPasswordReused: false;
    privateDatabaseRead: false;
    passwordReturned: false;
  };
};

export type EchoAccountStatusDesired = { username: string; active: boolean };
export type EchoAccountPasswordDesired = {
  username: string;
  newPassword: string;
};
export type EchoAccountUnlinkDesired = { username: string };
export type EchoAccountLifecyclePlan = {
  schema: "echo.account-directory.v1";
  planId: string;
  operation: "setMemberStatus" | "resetMemberPassword" | "unlinkMember";
  requiresApproval: true;
  account: { username: string; active: boolean };
  changes: string[];
  safety?: Record<string, boolean>;
};

async function responseError(response: Response, fallback: string) {
  const detail = await response
    .json()
    .then((body) => body?.detail)
    .catch(() => null);
  return new Error(detail || fallback);
}

export async function fetchEchoAccounts(): Promise<EchoAccountDirectory> {
  const response = await fetch("/api/appliance/accounts", {
    headers: authHeader(),
  });
  if (!response.ok)
    throw await responseError(response, "无法读取 Echo 家庭账号");
  return (await response.json()) as EchoAccountDirectory;
}

export async function planEchoAccountLink(
  desired: EchoAccountLinkDesired,
): Promise<EchoAccountLinkPlan> {
  const response = await fetch("/api/appliance/accounts/link/plan", {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify(desired),
  });
  if (!response.ok)
    throw await responseError(response, "无法预览 Echo 登录开通");
  return (await response.json()) as EchoAccountLinkPlan;
}

export async function applyEchoAccountLink(
  desired: EchoAccountLinkDesired,
  planId: string,
  approvalToken: string,
): Promise<{ linked: true; account: EchoAccountLinkPlan["account"] }> {
  const response = await fetch("/api/appliance/accounts/link/apply", {
    method: "POST",
    headers: {
      ...authHeader(),
      ...approvalHeader(approvalToken),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ planId, desired }),
  });
  if (!response.ok) throw await responseError(response, "Echo 登录开通失败");
  return (await response.json()) as {
    linked: true;
    account: EchoAccountLinkPlan["account"];
  };
}

async function planLifecycle(
  path: string,
  desired:
    | EchoAccountStatusDesired
    | EchoAccountPasswordDesired
    | EchoAccountUnlinkDesired,
  fallback: string,
): Promise<EchoAccountLifecyclePlan> {
  const response = await fetch(path, {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify(desired),
  });
  if (!response.ok) throw await responseError(response, fallback);
  return (await response.json()) as EchoAccountLifecyclePlan;
}

async function applyLifecycle(
  path: string,
  desired:
    | EchoAccountStatusDesired
    | EchoAccountPasswordDesired
    | EchoAccountUnlinkDesired,
  planId: string,
  approvalToken: string,
  fallback: string,
) {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      ...authHeader(),
      ...approvalHeader(approvalToken),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ planId, desired }),
  });
  if (!response.ok) throw await responseError(response, fallback);
  return (await response.json()) as {
    updated?: true;
    unlinked?: true;
    sessionsRevoked: true;
    sessionNotBefore: number;
  };
}

export function planEchoAccountStatus(desired: EchoAccountStatusDesired) {
  return planLifecycle(
    "/api/appliance/accounts/status/plan",
    desired,
    "无法预览成员状态变更",
  );
}

export function applyEchoAccountStatus(
  desired: EchoAccountStatusDesired,
  planId: string,
  approvalToken: string,
) {
  return applyLifecycle(
    "/api/appliance/accounts/status/apply",
    desired,
    planId,
    approvalToken,
    "成员状态变更失败",
  );
}

export function planEchoAccountPassword(desired: EchoAccountPasswordDesired) {
  return planLifecycle(
    "/api/appliance/accounts/password/plan",
    desired,
    "无法预览 Echo 密码重置",
  );
}

export function applyEchoAccountPassword(
  desired: EchoAccountPasswordDesired,
  planId: string,
  approvalToken: string,
) {
  return applyLifecycle(
    "/api/appliance/accounts/password/apply",
    desired,
    planId,
    approvalToken,
    "Echo 密码重置失败",
  );
}

export function planEchoAccountUnlink(desired: EchoAccountUnlinkDesired) {
  return planLifecycle(
    "/api/appliance/accounts/unlink/plan",
    desired,
    "无法预览 Echo 登录移除",
  );
}

export function applyEchoAccountUnlink(
  desired: EchoAccountUnlinkDesired,
  planId: string,
  approvalToken: string,
) {
  return applyLifecycle(
    "/api/appliance/accounts/unlink/apply",
    desired,
    planId,
    approvalToken,
    "Echo 登录移除失败",
  );
}
