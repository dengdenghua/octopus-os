import { approvalHeader } from "@/appliance/approval";
import { authHeader } from "@/appliance/auth";

export type FileShare = {
  id: string;
  filename: string;
  createdAt: number;
  expiresAt: number;
  maxDownloads: number;
  downloadCount: number;
  active?: boolean;
};

export type FileSharePlan = {
  schema: "echo.file-share.plan.v1";
  planId: string;
  operation: "create" | "revoke";
  requiresApproval: true;
  approval: {
    action: "files.share.create" | "files.share.revoke";
    target: string;
  };
};

async function jsonResponse<T>(
  response: Response,
  fallback: string,
): Promise<T> {
  if (response.ok) return (await response.json()) as T;
  const detail = await response
    .json()
    .then((value) => value?.detail)
    .catch(() => null);
  throw new Error(typeof detail === "string" ? detail : fallback);
}

export async function listFileShares(): Promise<FileShare[]> {
  const response = await fetch("/api/appliance/file-shares", {
    headers: authHeader(),
  });
  const payload = await jsonResponse<{ shares: FileShare[] }>(
    response,
    "读取分享链接失败",
  );
  return payload.shares;
}

export async function planFileShare(
  path: string,
  ttlSeconds = 7 * 24 * 3600,
  maxDownloads = 100,
): Promise<FileSharePlan> {
  const response = await fetch("/api/appliance/file-shares/plans", {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ path, ttlSeconds, maxDownloads }),
  });
  return jsonResponse(response, "生成分享方案失败");
}

export async function applyFileShare(
  path: string,
  planId: string,
  approvalToken: string,
  ttlSeconds = 7 * 24 * 3600,
  maxDownloads = 100,
): Promise<{ share: FileShare; url: string }> {
  const response = await fetch("/api/appliance/file-shares/apply", {
    method: "POST",
    headers: {
      ...authHeader(),
      ...approvalHeader(approvalToken),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ path, ttlSeconds, maxDownloads, planId }),
  });
  return jsonResponse(response, "创建分享链接失败");
}

export async function planFileShareRevocation(
  shareId: string,
): Promise<FileSharePlan> {
  const response = await fetch(
    `/api/appliance/file-shares/${encodeURIComponent(shareId)}/revoke-plan`,
    { method: "POST", headers: authHeader() },
  );
  return jsonResponse(response, "生成撤销方案失败");
}

export async function revokeFileShare(
  shareId: string,
  planId: string,
  approvalToken: string,
): Promise<void> {
  const response = await fetch(
    `/api/appliance/file-shares/${encodeURIComponent(shareId)}/revoke`,
    {
      method: "POST",
      headers: {
        ...authHeader(),
        ...approvalHeader(approvalToken),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ planId }),
    },
  );
  await jsonResponse(response, "撤销分享链接失败");
}
