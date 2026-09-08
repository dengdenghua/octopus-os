import { approvalHeader } from "@/appliance/approval";
import { authHeader } from "@/appliance/auth";

export type NasBackupAttempt = {
  schema: "echo.nas-data-backup-schedule-result.v1";
  outcome: "completed" | "disabled" | "failed";
  completedAt: string;
  setId?: string;
  snapshotId?: string;
  repositoryId?: string;
  memberCount?: number;
  encrypted?: boolean;
  fullReadVerified?: boolean;
  idempotent?: boolean;
  errorCode?: string;
  pathsRedacted: true;
};

export type NasBackupScheduleStatus = {
  schemaVersion: 1;
  configured: boolean;
  enabled: boolean;
  repositoryConfigured: boolean;
  credentialConfigured: boolean;
  credentialRotationRecoveryPending: boolean;
  schedulerInstalled: boolean;
  timerEnabled: boolean;
  schedule: string;
  history: NasBackupAttempt[];
  pathsRedacted: true;
  source: "native";
};

export type NasBackupScheduleDesired = {
  schema: "echo.nas-data-backup-schedule.v1";
  enabled: boolean;
  repository: string | null;
  repositoryMount: string | null;
};

export type NasBackupCredentialDesired = {
  schema: "echo.nas-data-backup-credential-desired.v1";
  mode: "initialize" | "connect";
  repository: string;
  repositoryMount: string;
  password: string;
};

export type NasBackupCredentialPlan = {
  schema: "echo.nas-data-backup-credential-plan.v1";
  planId: string;
  operation: "initializeCredential" | "connectCredential";
  requiresApproval: true;
  desired: {
    mode: "initialize" | "connect";
    repositoryConfigured: true;
    passwordBound: true;
  };
  pathsRedacted: true;
  safety: {
    systemdEncryptedCredential: true;
    externalMountedRepositoryRequired: true;
    existingCredentialMustBeAbsent: true;
    blindRotationAllowed: false;
  };
  applied?: boolean;
  verified?: boolean;
  repositoryId?: string;
  repositoryVerified?: boolean;
  fullReadVerified?: boolean;
};

export type NasBackupCredentialRotationDesired = {
  schema: "echo.nas-data-backup-credential-rotation-desired.v1";
  repository: string;
  repositoryMount: string;
  currentPassword: string;
  newPassword: string;
};

export type NasBackupCredentialRotationPlan = {
  schema: "echo.nas-data-backup-credential-rotation-plan.v1";
  planId: string;
  operation: "rotateCredential";
  requiresApproval: true;
  desired: {
    repositoryConfigured: true;
    currentPasswordBound: true;
    newPasswordBound: true;
  };
  pathsRedacted: true;
  safety: {
    systemdEncryptedCredential: true;
    newRepositoryKeyVerifiedBeforeSwitch: true;
    oldPasswordKeysRevokedAfterSwitch: true;
    rollbackPreservesRepositoryAccess: true;
  };
  applied?: boolean;
  verified?: boolean;
  repositoryId?: string;
  repositoryVerified?: boolean;
  oldPasswordRevoked?: boolean;
  removedKeyCount?: number;
};

export type NasBackupSchedulePlan = {
  schema: "echo.nas-data-backup-schedule-plan.v1";
  planId: string;
  operation: "none" | "enable" | "update" | "disable";
  requiresApproval: boolean;
  current: {
    enabled: boolean;
    repositoryConfigured: boolean;
    timerEnabled: boolean;
  };
  desired: { enabled: boolean; repositoryConfigured: boolean };
  schedule: string;
  pathsRedacted: true;
  safety: {
    encryptedCredentialRequired: true;
    externalMountedRepositoryRequired: true;
    managedReadOnlyBtrfsSnapshotsOnly: true;
    fullRepositoryReadAfterBackup: true;
  };
  applied?: boolean;
  verified?: boolean;
};

export type NasBackupRestoreMember = {
  sharedFolderRef: string;
  sourceSharedFolderRef?: string;
  targetSharedFolderRef?: string;
  filesystemUuid: string;
  targetFilesystemUuid?: string;
  snapshotId: string;
  targetSubvolumeUuid?: string;
  remapped?: boolean;
  contentVerified?: boolean;
};

export type NasBackupRestoreSet = {
  setId: string;
  snapshotId: string;
  createdAt: string;
  manifestSha256: string;
  memberCount: number;
  members: NasBackupRestoreMember[];
};

export type NasBackupRestoreSetList = {
  schema: "echo.nas-data-backup-restore-set-list.v1";
  repositoryId: string;
  setCount: number;
  sets: NasBackupRestoreSet[];
  truncated: boolean;
  encrypted: true;
  pathsRedacted: true;
  verification: "authenticated_index_only";
  restoreMode: "explicit-empty-managed-btrfs-target-mapping";
};

export type NasBackupRestoreTarget = {
  sharedFolderRef: string;
  name: string;
  filesystemUuid: string;
  empty: boolean;
};

export type NasBackupRestoreTargetList = {
  schema: "echo.nas-data-backup-restore-target-list.v1";
  targetCount: number;
  targets: NasBackupRestoreTarget[];
  pathsRedacted: true;
};

export type NasBackupRestoreRepository = {
  schema: "echo.nas-data-backup-restore-repository.v1";
  repository: string;
  repositoryMount: string;
};

export type NasBackupRestoreDesired = {
  schema: "echo.nas-data-backup-restore-desired.v2";
  selector: string;
  repository: string;
  repositoryMount: string;
  targets: Array<{
    sourceSharedFolderRef: string;
    targetSharedFolderRef: string;
  }>;
};

export type NasBackupRestorePlan = {
  schema: "echo.nas-data-backup-restore-plan.v1";
  planId: string;
  operation: "restoreBackupSet" | "resumeRestore" | "verifyRestore";
  requiresApproval: true;
  repositoryId: string;
  snapshotId: string;
  setId: string;
  manifestSha256: string;
  memberCount: number;
  members: NasBackupRestoreMember[];
  confirmation: string;
  recoveryPending: boolean;
  pathsRedacted: true;
  safety: {
    originalManagedBtrfsTargetsOnly: false;
    emptyTargetsRequired: boolean;
    networkSharesMustBeUnpublished: true;
    scheduledWritersMustBeDisabled: true;
    fullRepositoryReadBeforePromotion: true;
    perShareAtomicPromotion: true;
    durableResumeReceipt: true;
    replacementDiskMappingSupported: true;
  };
};

export type NasBackupRestoreResult = {
  schema: "echo.nas-data-backup-restore-result.v1";
  planId: string;
  repositoryId: string;
  snapshotId: string;
  setId: string;
  manifestSha256: string;
  memberCount: number;
  members: NasBackupRestoreMember[];
  fullReadVerified: true;
  contentVerified: true;
  pathsRedacted: true;
  phase: "verified";
  recovery: "fresh_restore" | "resumed";
  verified: true;
};

async function responseError(response: Response, fallback: string) {
  const detail = await response
    .json()
    .then((body) => body?.detail)
    .catch(() => null);
  if (response.status === 401) return new Error("登录已失效，请重新登录");
  if (response.status === 409)
    return new Error(detail || "备份策略状态已变化，请重新预览");
  return new Error(detail || fallback);
}

export async function fetchNasBackupSchedule() {
  const response = await fetch("/api/appliance/storage/backups/schedule", {
    headers: authHeader(),
  });
  if (!response.ok)
    throw await responseError(response, "无法读取 NAS 备份状态");
  return (await response.json()) as NasBackupScheduleStatus;
}

export function fetchNasBackupRestoreSets(
  repository: NasBackupRestoreRepository,
  limit = 50,
) {
  return postJson<NasBackupRestoreSetList>(
    `/api/appliance/storage/backups/restore/sets?limit=${encodeURIComponent(limit)}`,
    repository,
    "无法读取 NAS 可恢复版本",
  );
}

export async function fetchNasBackupRestoreTargets() {
  const response = await fetch(
    "/api/appliance/storage/backups/restore/targets",
    { headers: authHeader() },
  );
  if (!response.ok)
    throw await responseError(response, "无法读取 NAS 恢复目标");
  return (await response.json()) as NasBackupRestoreTargetList;
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

export function planNasBackupSchedule(desired: NasBackupScheduleDesired) {
  return postJson<NasBackupSchedulePlan>(
    "/api/appliance/storage/backups/schedule/plan",
    desired,
    "无法生成 NAS 备份策略预览",
  );
}

export function planNasBackupCredential(desired: NasBackupCredentialDesired) {
  return postJson<NasBackupCredentialPlan>(
    "/api/appliance/storage/backups/credential/plan",
    desired,
    "无法生成 NAS 备份凭据预览",
  );
}

export function applyNasBackupCredential(
  desired: NasBackupCredentialDesired,
  planId: string,
  approvalToken: string,
) {
  return postJson<NasBackupCredentialPlan>(
    "/api/appliance/storage/backups/credential/apply",
    { desired, planId },
    "无法配置 NAS 备份凭据",
    approvalToken,
  );
}

export function planNasBackupCredentialRotation(
  desired: NasBackupCredentialRotationDesired,
) {
  return postJson<NasBackupCredentialRotationPlan>(
    "/api/appliance/storage/backups/credential/rotation/plan",
    desired,
    "无法生成 NAS 备份凭据轮换预览",
  );
}

export function applyNasBackupCredentialRotation(
  desired: NasBackupCredentialRotationDesired,
  planId: string,
  approvalToken: string,
) {
  return postJson<NasBackupCredentialRotationPlan>(
    "/api/appliance/storage/backups/credential/rotation/apply",
    { desired, planId },
    "无法轮换 NAS 备份凭据",
    approvalToken,
  );
}

export function applyNasBackupSchedule(
  desired: NasBackupScheduleDesired,
  planId: string,
  approvalToken: string,
) {
  return postJson<NasBackupSchedulePlan>(
    "/api/appliance/storage/backups/schedule/apply",
    { desired, planId },
    "无法更新 NAS 备份策略",
    approvalToken,
  );
}

export function planNasBackupRestore(desired: NasBackupRestoreDesired) {
  return postJson<NasBackupRestorePlan>(
    "/api/appliance/storage/backups/restore/plan",
    desired,
    "无法生成 NAS 数据恢复预览",
  );
}

export function applyNasBackupRestore(
  desired: NasBackupRestoreDesired,
  planId: string,
  confirmation: string,
  approvalToken: string,
) {
  return postJson<NasBackupRestoreResult>(
    "/api/appliance/storage/backups/restore/apply",
    { desired, planId, confirmation },
    "无法恢复 NAS 数据",
    approvalToken,
  );
}
