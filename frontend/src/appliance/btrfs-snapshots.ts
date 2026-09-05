import { approvalHeader } from "@/appliance/approval";
import { authHeader } from "@/appliance/auth";

export type BtrfsSnapshot = {
  snapshotId: string;
  name: string;
  subvolumeUuid: string;
  readOnly: true;
  kind: "manual" | "automatic";
};

export type BtrfsSnapshotSchedule = {
  schemaVersion: 1;
  sharedFolderRef: string;
  enabled: boolean;
  keepLatest: number;
  configured: boolean;
  schedulerInstalled: boolean;
  schedule: string;
  scope: "automaticSnapshotsOnly";
};

export type BtrfsSnapshotScheduleDesired = {
  schema: "echo.btrfs-snapshot-schedule-desired.v1";
  sharedFolderRef: string;
  enabled: boolean;
  keepLatest: number;
};

export type BtrfsSnapshotSchedulePlan = {
  planId: string;
  operation: "none" | "enable" | "update" | "disable";
  requiresApproval: boolean;
  desired: BtrfsSnapshotScheduleDesired;
  schedule: string;
  scope: "automaticSnapshotsOnly";
  applied?: boolean;
  verified?: boolean;
};

export type BtrfsSnapshotDesired = {
  schema: "echo.omv.btrfs-snapshot-desired.v1";
  sharedFolderRef: string;
  name: string;
};

export type BtrfsSnapshotDeleteDesired = {
  schema: "echo.omv.btrfs-snapshot-delete-desired.v1";
  sharedFolderRef: string;
  snapshotId: string;
};

export type BtrfsSnapshotPlan = {
  schema: "echo.omv.btrfs-snapshot-plan.v1";
  planId: string;
  operation: "create" | "none";
  requiresApproval: boolean;
  desired: BtrfsSnapshotDesired;
  snapshot: BtrfsSnapshot | null;
  safety: {
    readOnly: true;
    sameFilesystem: true;
    applicationQuiesce: false;
    maximumPerShare: 256;
    restoreSupported: false;
  };
  applied?: boolean;
  verified?: boolean;
};

export type BtrfsSnapshotDeletePlan = {
  schema: "echo.omv.btrfs-snapshot-delete-plan.v1";
  planId: string;
  operation: "delete";
  requiresApproval: true;
  desired: BtrfsSnapshotDeleteDesired;
  snapshot: BtrfsSnapshot;
  applied?: boolean;
  verified?: boolean;
};

async function responseError(response: Response, fallback: string) {
  const detail = await response
    .json()
    .then((body) => body?.detail)
    .catch(() => null);
  if (response.status === 401) return new Error("登录已失效，请重新登录");
  if (response.status === 409)
    return new Error(detail || "快照状态已变化，请重新预览");
  return new Error(detail || fallback);
}

async function postJson<T>(
  url: string,
  body: unknown,
  fallback: string,
  approvalToken?: string,
): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      ...authHeader(),
      ...(approvalToken ? approvalHeader(approvalToken) : {}),
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw await responseError(response, fallback);
  return (await response.json()) as T;
}

export async function fetchBtrfsSnapshots(sharedFolderRef: string) {
  const response = await fetch(
    `/api/appliance/omv/sharing/${encodeURIComponent(sharedFolderRef)}/snapshots`,
    { headers: authHeader() },
  );
  if (!response.ok)
    throw await responseError(response, "无法读取共享文件夹快照");
  return (await response.json()) as {
    sharedFolderRef: string;
    snapshots: BtrfsSnapshot[];
    limit: 256;
  };
}

export function planBtrfsSnapshot(desired: BtrfsSnapshotDesired) {
  return postJson<BtrfsSnapshotPlan>(
    "/api/appliance/omv/sharing/snapshots/plan",
    desired,
    "无法生成只读快照预览",
  );
}

export function applyBtrfsSnapshot(
  desired: BtrfsSnapshotDesired,
  planId: string,
  approvalToken: string,
) {
  return postJson<BtrfsSnapshotPlan>(
    "/api/appliance/omv/sharing/snapshots/apply",
    { desired, planId },
    "无法创建只读快照",
    approvalToken,
  );
}

export function planBtrfsSnapshotDelete(desired: BtrfsSnapshotDeleteDesired) {
  return postJson<BtrfsSnapshotDeletePlan>(
    "/api/appliance/omv/sharing/snapshots/delete/plan",
    desired,
    "无法生成快照删除预览",
  );
}

export function applyBtrfsSnapshotDelete(
  desired: BtrfsSnapshotDeleteDesired,
  planId: string,
  approvalToken: string,
) {
  return postJson<BtrfsSnapshotDeletePlan>(
    "/api/appliance/omv/sharing/snapshots/delete/apply",
    { desired, planId },
    "无法删除只读快照",
    approvalToken,
  );
}

export async function fetchBtrfsSnapshotSchedule(sharedFolderRef: string) {
  const response = await fetch(
    `/api/appliance/omv/sharing/${encodeURIComponent(sharedFolderRef)}/snapshots/schedule`,
    { headers: authHeader() },
  );
  if (!response.ok) throw await responseError(response, "无法读取自动快照策略");
  return (await response.json()) as BtrfsSnapshotSchedule;
}

export function planBtrfsSnapshotSchedule(
  desired: BtrfsSnapshotScheduleDesired,
) {
  return postJson<BtrfsSnapshotSchedulePlan>(
    "/api/appliance/omv/sharing/snapshots/schedule/plan",
    desired,
    "无法生成自动快照策略预览",
  );
}

export function applyBtrfsSnapshotSchedule(
  desired: BtrfsSnapshotScheduleDesired,
  planId: string,
  approvalToken: string,
) {
  return postJson<BtrfsSnapshotSchedulePlan>(
    "/api/appliance/omv/sharing/snapshots/schedule/apply",
    { desired, planId },
    "无法更新自动快照策略",
    approvalToken,
  );
}
