import { authHeader } from "@/appliance/auth";
import { approvalHeader } from "@/appliance/approval";

export type OmvStatus = {
  configured: boolean;
  available: boolean;
  readOnly: boolean;
  adminUrl: string | null;
  capabilities: string[];
};

export type OmvFilesystem = {
  devicefile: string;
  parentdevicefile: string | null;
  uuid: string | null;
  label: string;
  type: string;
  mountpoint: string;
  sizeBytes: number;
  availableBytes: number;
  usedPercent: number | null;
  readOnly: boolean;
  supportsAcl: boolean;
  supportsQuota: boolean;
};

export type OmvSmart = {
  devicefile: string;
  model: string;
  health: string;
  temperatureC: number | null;
  powerOnHours: number | null;
  powerCycles: number | null;
};

export type OmvSmartDevice = {
  devicefile: string;
  model: string;
  sizeBytes: number | null;
  health: string;
  temperatureC: number | null;
};

export type OmvTopologyDevice = {
  devicefile: string;
  type: string;
  sizeBytes: number | null;
  filesystemType: string | null;
  rotational: boolean | null;
  parentDevicefiles: string[];
};

export type OmvRaidArray = {
  devicefile: string;
  level: string;
  status:
    | "healthy"
    | "degraded"
    | "recovering"
    | "checking"
    | "inactive"
    | "unknown"
    | string;
  totalDevices: number | null;
  activeDevices: number | null;
  operation: string | null;
  operationPercent: number | null;
};

export type OmvStorageTopology = {
  devices: OmvTopologyDevice[];
  arrays: OmvRaidArray[];
};

export type OmvHealthAlert = {
  id: string;
  code: string;
  severity: "warning" | "critical";
  resource: string;
  message: string;
  firstSeenAt: string;
  lastSeenAt: string;
  occurrences: number;
};

export type OmvHealthEvent = {
  id: string;
  alertId: string;
  event: "opened" | "changed" | "resolved";
  at: string;
  code: string;
  severity: "warning" | "critical";
  resource: string;
  message: string;
};

export type OmvHealthSnapshot = {
  schemaVersion: number;
  state:
    | "notConfigured"
    | "pending"
    | "healthy"
    | "warning"
    | "critical"
    | "unavailable";
  stale: boolean;
  checkedAt: string | null;
  lastSuccessfulAt: string | null;
  intervalSeconds: number;
  persistenceHealthy: boolean;
  monitoring: boolean;
  activeAlerts: OmvHealthAlert[];
  events: OmvHealthEvent[];
  summary: { critical: number; warning: number; total: number };
  readOnly: true;
};

export type OmvSharedFolder = {
  uuid: string;
  name: string;
  comment: string;
  relativePath: string;
  device: string;
  status: string;
  inUse: boolean;
  supportsAcl: boolean;
};

export type OmvSharedFolderTarget = {
  mountPointRef: string;
  filesystemUuid: string | null;
  label: string;
  type: string;
  sizeBytes: number;
  availableBytes: number;
  readOnly: false;
};

export type OmvNasUser = {
  name: string;
  uid: number;
  gid: number;
  comment: string;
  groups: string[];
};

export type OmvNasGroup = {
  name: string;
  gid: number;
  members: string[];
};

export type OmvSmbShare = {
  uuid: string;
  sharedFolderRef: string;
  sharedFolderName: string;
  enabled: boolean;
  readOnly: boolean;
  guest: string;
  browseable: boolean;
  recycleBin: boolean;
  comment: string;
};

export type OmvNfsShare = {
  uuid: string;
  sharedFolderRef: string;
  sharedFolderName: string;
  client: string;
  options: string;
  comment: string;
};

export type OmvSharingOverview = {
  sharedFolders: OmvSharedFolder[];
  sharedFolderTargets: OmvSharedFolderTarget[];
  users: OmvNasUser[];
  groups: OmvNasGroup[];
  smb: { enabled: boolean; shares: OmvSmbShare[] };
  nfs: { enabled: boolean; shares: OmvNfsShare[] };
};

export type OmvGroupDesiredState = {
  schema: "echo.omv.group-desired.v1";
  name: string;
  comment: string;
};

export type OmvGroupPlan = {
  schema: "echo.omv.group-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "create";
  requiresApproval: true;
  desired: OmvGroupDesiredState;
  changes: Array<{
    field: "name" | "comment";
    before: null;
    after: string;
  }>;
  safety: {
    scope: "newNormalOmvGroup";
    initialMembers: "empty";
    systemGroups: "never";
    update: "notManaged";
    delete: "rollbackOnlyBeforeUse";
  };
  applied?: boolean;
  verified?: boolean;
};

export type OmvUserDesiredState = {
  schema: "echo.omv.user-desired.v1";
  name: string;
  displayName: string;
  password: string;
  groups: string[];
};

export type OmvUserPlan = {
  schema: "echo.omv.user-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "create";
  requiresApproval: true;
  desired: {
    schema: "echo.omv.user-desired.v1";
    name: string;
    displayName: string;
    groups: string[];
    passwordBound: true;
  };
  changes: Array<{
    field: "name" | "displayName" | "groups";
    before: null | [];
    after: string | string[];
  }>;
  safety: {
    scope: "newNormalOmvUser";
    password: "hmacBoundNeverReturnedOrAudited";
    loginShell: "nologin";
    sshKeys: "none";
    homeDirectory: "automaticHomesMustBeDisabled";
    systemGroups: "notEnumeratedNotSelectable";
    update: "notManaged";
    delete: "rollbackOnlyBeforeUse";
  };
  applied?: boolean;
  verified?: boolean;
};

export type OmvUserPasswordDesiredState = {
  schema: "echo.omv.user-password-desired.v1";
  name: string;
  password: string;
};

export type OmvUserPasswordPlan = {
  schema: "echo.omv.user-password-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "resetPassword";
  requiresApproval: true;
  desired: {
    schema: "echo.omv.user-password-desired.v1";
    name: string;
    passwordBound: true;
  };
  changes: Array<{
    field: "password";
    before: "currentCredential";
    after: "replacementCredential";
  }>;
  safety: {
    scope: "existingConstrainedNormalOmvUser";
    password: "hmacBoundNeverReturnedOrAudited";
    accountFields: "preservedAndVerified";
    loginShell: "nologin";
    sshKeys: "none";
    rollback: "notAvailableAfterAcceptedSecretRpc";
  };
  applied?: boolean;
  verified?: boolean;
};

export type OmvSharedFolderDesiredState = {
  schema: "echo.omv.shared-folder-desired.v1";
  mountPointRef: string;
  name: string;
  comment: string;
};

export type OmvSharedFolderPlan = {
  schema: "echo.omv.shared-folder-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "create" | "none";
  requiresApproval: boolean;
  shareUuid: string;
  target: OmvSharedFolderTarget;
  desired: OmvSharedFolderDesiredState;
  changes: Array<{
    field: "name" | "comment";
    before: null;
    after: string;
  }>;
  safety: {
    filesystem: "existingMountedWritableOnly";
    relativePath: "derivedFromPortableName";
    directoryMode: "2770UsersGroup";
    acl: "notManaged";
    update: "notManaged";
    delete: "notManaged";
  };
  applied?: boolean;
  verified?: boolean;
};

export type OmvSharePrivilege = {
  type: "user" | "group";
  id: number;
  name: string;
  permission: OmvSharePermission;
};

export type OmvSharePermission = "inherit" | "none" | "read" | "readWrite";

export type OmvSharePrivilegeDesiredState = {
  schema: "echo.omv.share-privilege-desired.v1";
  sharedFolderRef: string;
  principalType: "user" | "group";
  principalName: string;
  permission: OmvSharePermission;
};

export type OmvSharePrivilegePlan = {
  schema: "echo.omv.share-privilege-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "update" | "none";
  requiresApproval: boolean;
  sharedFolder: { uuid: string; name: string; status: string };
  principal: {
    type: "user" | "group";
    id: number;
    name: string;
    before: OmvSharePermission;
    after: OmvSharePermission;
  };
  desired: OmvSharePrivilegeDesiredState;
  changes: Array<{
    field: "permission";
    before: OmvSharePermission;
    after: OmvSharePermission;
  }>;
  safety: {
    scope: "sharedFolderConfigPrivilege";
    principal: "existingOmvUserOrGroup";
    filesystemAcl: "notModified";
    recursive: "never";
    serviceDeploy: "sambaAndRsyncdWhenDirty";
    delete: "notManaged";
  };
  applied?: boolean;
  verified?: boolean;
  deployedServices?: Array<"samba" | "rsyncd">;
};

export type OmvSmbDesiredState = {
  schema: "echo.omv.smb-share-desired.v1";
  sharedFolderRef: string;
  enabled: boolean;
  readOnly: boolean;
  browseable: boolean;
  recycleBin: boolean;
  comment: string;
};

export type OmvSmbPlanChange = {
  field: "enabled" | "readOnly" | "browseable" | "recycleBin" | "comment";
  before: boolean | string | null;
  after: boolean | string;
};

export type OmvSmbPlan = {
  schema: "echo.omv.smb-share-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "create" | "update" | "none";
  requiresApproval: boolean;
  shareUuid: string;
  sharedFolder: { uuid: string; name: string; status: string };
  desired: OmvSmbDesiredState;
  changes: OmvSmbPlanChange[];
  safety: Record<string, string>;
  applied?: boolean;
  verified?: boolean;
};

export type OmvNfsDesiredState = {
  schema: "echo.omv.nfs-share-desired.v1";
  sharedFolderRef: string;
  clientCidr: string;
  readOnly: boolean;
  comment: string;
};

export type OmvNfsPlan = {
  schema: "echo.omv.nfs-share-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "create" | "update" | "none";
  requiresApproval: boolean;
  shareUuid: string;
  sharedFolder: { uuid: string; name: string; status: string };
  desired: OmvNfsDesiredState;
  changes: Array<{
    field: "readOnly" | "comment";
    before: boolean | string | null;
    after: boolean | string;
  }>;
  safety: {
    clientScope: "privateCidrOnly";
    rootSquash: "required";
    syncWrites: "required";
    advancedOptions: "notManaged";
    delete: "notManaged";
  };
  applied?: boolean;
  verified?: boolean;
};

export type OmvQuotaDesiredState = {
  schema: "echo.omv.filesystem-quota-desired.v1";
  filesystemUuid: string;
  subjectType: "user" | "group";
  subjectName: string;
  hardLimitBytes: number;
};

export type OmvQuotaPlan = {
  schema: "echo.omv.filesystem-quota-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "update" | "none";
  requiresApproval: boolean;
  filesystem: {
    uuid: string;
    label: string;
    type: string;
    readOnly: false;
    supportsQuota: true;
  };
  subject: {
    type: "user" | "group";
    name: string;
    hardLimitBytes: number;
    used: string;
  };
  desired: OmvQuotaDesiredState;
  changes: Array<{
    field: "hardLimitBytes";
    before: number;
    after: number;
  }>;
  safety: {
    scope: "filesystemUserOrGroup";
    protocolCoverage: ["local", "SMB", "NFS"];
    sharedFolderQuota: "notSupportedByOmvQuotaRpc";
    minimumUnitBytes: 1024;
  };
  applied?: boolean;
  verified?: boolean;
};

async function readJson<T>(url: string, fallback: string): Promise<T> {
  const response = await fetch(url, { headers: authHeader() });
  if (!response.ok) {
    const detail = await response
      .json()
      .then((body) => body?.detail)
      .catch(() => null);
    if (response.status === 401) throw new Error("登录已失效，请重新登录");
    if (response.status === 404) throw new Error(fallback);
    throw new Error(detail || fallback);
  }
  return (await response.json()) as T;
}

async function postJson<T>(
  url: string,
  body: unknown,
  fallback: string,
  extraHeaders: Record<string, string> = {},
): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      ...authHeader(),
      ...extraHeaders,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await response
      .json()
      .then((value) => value?.detail)
      .catch(() => null);
    if (response.status === 401) throw new Error("登录已失效，请重新登录");
    if (response.status === 404) throw new Error(fallback);
    if (response.status === 409)
      throw new Error(detail || "配置已变化，请重新预览");
    throw new Error(detail || fallback);
  }
  return (await response.json()) as T;
}

export function fetchOmvStatus(): Promise<OmvStatus> {
  return readJson("/api/appliance/omv/status", "无法读取 OMV 接入状态");
}

export async function fetchOmvFilesystems(): Promise<OmvFilesystem[]> {
  const result = await readJson<{ filesystems: OmvFilesystem[] }>(
    "/api/appliance/omv/filesystems",
    "无法读取 OMV 存储卷",
  );
  return result.filesystems;
}

export async function fetchOmvSmart(devicefile: string): Promise<OmvSmart> {
  const result = await readJson<{ smart: OmvSmart }>(
    `/api/appliance/omv/smart?devicefile=${encodeURIComponent(devicefile)}`,
    "无法读取磁盘 SMART 状态",
  );
  return result.smart;
}

export async function fetchOmvSmartDevices(): Promise<OmvSmartDevice[]> {
  const result = await readJson<{ devices: OmvSmartDevice[] }>(
    "/api/appliance/omv/smart/devices",
    "无法读取 OMV 物理磁盘",
  );
  return result.devices;
}

export async function fetchOmvStorageTopology(): Promise<OmvStorageTopology> {
  return readJson<OmvStorageTopology>(
    "/api/appliance/omv/topology",
    "无法读取磁盘、阵列与逻辑卷关系",
  );
}

export function fetchOmvHealth(): Promise<OmvHealthSnapshot> {
  return readJson<OmvHealthSnapshot>(
    "/api/appliance/omv/health",
    "无法读取持续存储监测状态",
  );
}

export function fetchOmvSharingOverview(): Promise<OmvSharingOverview> {
  return readJson<OmvSharingOverview>(
    "/api/appliance/omv/sharing",
    "无法读取共享、用户和权限概览",
  );
}

export function planOmvGroup(
  desired: OmvGroupDesiredState,
): Promise<OmvGroupPlan> {
  return postJson(
    "/api/appliance/omv/accounts/groups/plan",
    desired,
    "无法生成用户组创建预览",
  );
}

export function applyOmvGroup(
  desired: OmvGroupDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvGroupPlan> {
  return postJson(
    "/api/appliance/omv/accounts/groups/apply",
    { desired, planId },
    "无法创建用户组",
    approvalHeader(approvalToken),
  );
}

export function planOmvUser(
  desired: OmvUserDesiredState,
): Promise<OmvUserPlan> {
  return postJson(
    "/api/appliance/omv/accounts/users/plan",
    desired,
    "无法生成家庭成员创建预览",
  );
}

export function applyOmvUser(
  desired: OmvUserDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvUserPlan> {
  return postJson(
    "/api/appliance/omv/accounts/users/apply",
    { desired, planId },
    "无法创建家庭成员",
    approvalHeader(approvalToken),
  );
}

export function planOmvUserPassword(
  desired: OmvUserPasswordDesiredState,
): Promise<OmvUserPasswordPlan> {
  return postJson(
    "/api/appliance/omv/accounts/users/password/plan",
    desired,
    "无法生成成员密码重置预览",
  );
}

export function applyOmvUserPassword(
  desired: OmvUserPasswordDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvUserPasswordPlan> {
  return postJson(
    "/api/appliance/omv/accounts/users/password/apply",
    { desired, planId },
    "无法重置成员密码",
    approvalHeader(approvalToken),
  );
}

export function planOmvSharedFolder(
  desired: OmvSharedFolderDesiredState,
): Promise<OmvSharedFolderPlan> {
  return postJson(
    "/api/appliance/omv/sharing/folders/plan",
    desired,
    "无法生成共享文件夹创建预览",
  );
}

export function applyOmvSharedFolder(
  desired: OmvSharedFolderDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvSharedFolderPlan> {
  return postJson(
    "/api/appliance/omv/sharing/folders/apply",
    { desired, planId },
    "无法创建共享文件夹",
    approvalHeader(approvalToken),
  );
}

export async function fetchOmvSharePrivileges(
  shareUuid: string,
): Promise<OmvSharePrivilege[]> {
  const result = await readJson<{ privileges: OmvSharePrivilege[] }>(
    `/api/appliance/omv/sharing/${encodeURIComponent(shareUuid)}/privileges`,
    "无法读取共享权限",
  );
  return result.privileges;
}

export function planOmvSharePrivilege(
  desired: OmvSharePrivilegeDesiredState,
): Promise<OmvSharePrivilegePlan> {
  return postJson(
    "/api/appliance/omv/sharing/privileges/plan",
    desired,
    "无法生成共享权限变更预览",
  );
}

export function applyOmvSharePrivilege(
  desired: OmvSharePrivilegeDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvSharePrivilegePlan> {
  return postJson(
    "/api/appliance/omv/sharing/privileges/apply",
    { desired, planId },
    "无法应用共享权限",
    approvalHeader(approvalToken),
  );
}

export function planOmvSmbShare(
  desired: OmvSmbDesiredState,
): Promise<OmvSmbPlan> {
  return postJson(
    "/api/appliance/omv/sharing/smb/plan",
    desired,
    "无法生成 SMB 变更预览",
  );
}

export function applyOmvSmbShare(
  desired: OmvSmbDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvSmbPlan> {
  return postJson(
    "/api/appliance/omv/sharing/smb/apply",
    { desired, planId },
    "无法应用 SMB 配置",
    approvalHeader(approvalToken),
  );
}

export function planOmvNfsShare(
  desired: OmvNfsDesiredState,
): Promise<OmvNfsPlan> {
  return postJson(
    "/api/appliance/omv/sharing/nfs/plan",
    desired,
    "无法生成 NFS 变更预览",
  );
}

export function applyOmvNfsShare(
  desired: OmvNfsDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvNfsPlan> {
  return postJson(
    "/api/appliance/omv/sharing/nfs/apply",
    { desired, planId },
    "无法应用 NFS 配置",
    approvalHeader(approvalToken),
  );
}

export function planOmvFilesystemQuota(
  desired: OmvQuotaDesiredState,
): Promise<OmvQuotaPlan> {
  return postJson(
    "/api/appliance/omv/quota/plan",
    desired,
    "无法生成文件系统配额预览",
  );
}

export function applyOmvFilesystemQuota(
  desired: OmvQuotaDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvQuotaPlan> {
  return postJson(
    "/api/appliance/omv/quota/apply",
    { desired, planId },
    "无法应用文件系统配额",
    approvalHeader(approvalToken),
  );
}
