import { authHeader } from "@/appliance/auth";
import { approvalHeader } from "@/appliance/approval";

export type OmvStatus = {
  configured: boolean;
  available: boolean;
  readOnly: boolean;
  adminUrl: string | null;
  capabilities: string[];
  source?: "native";
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
  operation: "create" | "none";
  requiresApproval: boolean;
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
  operation: "create" | "none";
  requiresApproval: boolean;
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
  operation: "create" | "update" | "none";
  requiresApproval: boolean;
  shareUuid: string;
  target: OmvSharedFolderTarget;
  desired: OmvSharedFolderDesiredState;
  changes: Array<{
    field: "name" | "comment";
    before: string | null;
    after: string;
  }>;
  safety: {
    filesystem: "existingMountedWritableOnly";
    relativePath: "derivedFromPortableName";
    directoryMode: "2770UsersGroup";
    acl: "notManaged";
    update: "notManaged" | "commentOnly";
    delete: "notManaged";
  };
  applied?: boolean;
  verified?: boolean;
};

export type OmvSharedFolderRenameDesiredState = {
  schema: "echo.omv.shared-folder-rename-desired.v1";
  sharedFolderRef: string;
  name: string;
};

export type OmvSharedFolderRenamePlan = {
  schema: "echo.omv.shared-folder-rename-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "rename" | "none";
  requiresApproval: boolean;
  shareUuid: string;
  sharedFolder: OmvSharedFolder;
  desired: OmvSharedFolderRenameDesiredState;
  changes: Array<{
    field: "name";
    before: string;
    after: string;
  }>;
  safety: {
    filesystem: "sameMountedWritableVolume";
    data: "preserved";
    identity: "uuidPreserved";
    acl: "preservedWithDirectory";
    dependentShares: "mustBeAbsent";
    rollback: "directoryAndRegistry";
  };
  applied?: boolean;
  verified?: boolean;
  dataPreserved?: true;
};

export type OmvSharedFolderDetachDesiredState = {
  schema: "echo.omv.shared-folder-detach-desired.v1";
  sharedFolderRef: string;
  preserveData: true;
};

export type OmvSharedFolderDetachPlan = {
  schema: "echo.omv.shared-folder-detach-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "remove" | "none";
  requiresApproval: boolean;
  shareUuid: string;
  sharedFolder: OmvSharedFolder;
  desired: OmvSharedFolderDetachDesiredState;
  changes: Array<{
    field: "registration";
    before: "managed";
    after: "detached";
  }>;
  safety: {
    data: "preserved";
    directory: "neverDeleted";
    dependentShares: "mustBeAbsent";
    acl: "untouched";
    rollback: "registryOnly";
  };
  applied?: boolean;
  verified?: boolean;
  dataPreserved?: true;
};

export type OmvSharedFolderDeleteDesiredState = {
  schema: "echo.omv.shared-folder-delete-desired.v1";
  sharedFolderRef: string;
  emptyOnly: true;
};

export type OmvSharedFolderDeletePlan = {
  schema: "echo.omv.shared-folder-delete-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "remove";
  requiresApproval: true;
  shareUuid: string;
  sharedFolder: OmvSharedFolder;
  desired: OmvSharedFolderDeleteDesiredState;
  changes: Array<{
    field: "directory" | "registration";
    before: "empty" | "managed";
    after: "deleted" | "removed";
  }>;
  safety: {
    data: "emptyDirectoryOnly";
    directory: "deleted";
    dependentShares: "mustBeAbsent";
    recursive: "never";
    mount: "mountedWritableOnly";
    rollback: "registryAndEmptyDirectory";
  };
  applied?: boolean;
  verified?: boolean;
  directoryDeleted?: boolean;
  dataDeleted?: boolean;
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
  safety:
    | {
        scope: "sharedFolderConfigPrivilege";
        principal: "existingOmvUserOrGroup";
        filesystemAcl: "notModified";
        recursive: "never";
        serviceDeploy: "sambaAndRsyncdWhenDirty";
        delete: "notManaged";
      }
    | {
        scope: "registeredSharedFolderRootAcl";
        principal: "existingPosixUserOrGroup";
        filesystemAcl: "accessAndDefaultOnly";
        recursive: "never";
        rollback: "fullAclSnapshot";
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
  operation: "create" | "update" | "remove" | "none";
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

export type OmvNfsRemoveDesiredState = {
  schema: "echo.omv.nfs-share-remove-desired.v1";
  sharedFolderRef: string;
  clientCidr: string;
};

export type OmvNfsRemovePlan = {
  schema: "echo.omv.nfs-share-remove-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "remove";
  requiresApproval: true;
  shareUuid: string;
  sharedFolder: { uuid: string; name: string; status: string };
  desired: OmvNfsRemoveDesiredState;
  changes: Array<{
    field: "registration";
    before: "managed";
    after: "removed";
  }>;
  safety: {
    export: "managedRuleOnly";
    data: "preserved";
    directory: "neverModified";
    clientScope: "privateCidrOnly";
    rollback: "exportsAndLiveTable";
  };
  applied?: boolean;
  verified?: boolean;
  dataPreserved?: true;
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

export type OmvZfsMirrorCandidate = {
  devicefile: string;
  sizeBytes: number;
  serial: string | null;
  wwn: string | null;
  model: string | null;
};

export type OmvZfsMirrorDesiredState = {
  schema: "echo.omv.zfs-mirror-desired.v1";
  name: string;
  devices: [string, string];
  dataLossConfirmed: true;
};

export type OmvZfsMirrorPlan = {
  schema: "echo.omv.zfs-mirror-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "create";
  requiresApproval: true;
  desired: OmvZfsMirrorDesiredState;
  devices: [OmvZfsMirrorCandidate, OmvZfsMirrorCandidate];
  mountpoint: string;
  safety: {
    destructive: true;
    dataLossConfirmed: true;
    layout: "twoDiskMirrorOnly";
    devices: "wholeBlankNonRemovableWithPersistentIdentity";
    force: false;
    rollback: "bestEffortPoolDestroyBeforeHandoff";
    unsupported: string[];
  };
  applied?: boolean;
  verified?: boolean;
  pool?: {
    name: string;
    health: "ONLINE" | string;
    layout: "mirror";
    mountpoint: string;
    compression: string;
    atime: string;
    xattr: string;
    acltype: string;
  };
};

export type OmvMdRaid1Candidate = OmvZfsMirrorCandidate;

export type OmvMdRaid1DesiredState = {
  schema: "echo.omv.mdraid1-desired.v1";
  name: string;
  devices: [string, string];
  dataLossConfirmed: true;
};

export type OmvMdRaid1Plan = {
  schema: "echo.omv.mdraid1-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "create";
  target: string;
  requiresApproval: true;
  desired: OmvMdRaid1DesiredState;
  devices: [OmvMdRaid1Candidate, OmvMdRaid1Candidate];
  usableBytes: number;
  filesystemCreated: false;
  safety: {
    destructive: true;
    dataLossConfirmed: true;
    layout: "twoDiskRaid1Only";
    devices: "wholeBlankNonRemovableWithPersistentIdentity";
    force: false;
    degradedStart: false;
    filesystemCreated: false;
    unsupported: string[];
  };
  applied?: boolean;
  verified?: boolean;
  array?: {
    name: string;
    devicefile: string;
    uuid: string;
    level: "raid1";
    devices: [string, string] | string[];
    filesystem: null;
  };
};

export type OmvManagedMdRaid1 = NonNullable<OmvMdRaid1Plan["array"]>;

export type OmvBtrfsRaid1Candidate = OmvZfsMirrorCandidate;

export type OmvBtrfsRaid1DesiredState = {
  schema: "echo.omv.btrfs-raid1-desired.v1";
  name: string;
  devices: [string, string];
  dataLossConfirmed: true;
};

export type OmvBtrfsFilesystem = {
  uuid: string;
  mountpoint: string;
  dataProfile: "raid1" | string;
  metadataProfile: "raid1" | string;
  readOnly: boolean;
};

export type OmvBtrfsRaid1Plan = {
  schema: "echo.omv.btrfs-raid1-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "createAndMount";
  requiresApproval: true;
  desired: OmvBtrfsRaid1DesiredState;
  devices: [OmvBtrfsRaid1Candidate, OmvBtrfsRaid1Candidate];
  mountpoint: string;
  safety: {
    destructive: true;
    dataLossConfirmed: true;
    source: "twoBlankWholeDisksWithStableIdentityOnly";
    dataProfile: "raid1";
    metadataProfile: "raid1";
    mountRoot: string;
    persistentIdentity: "filesystemUuid";
    force: false;
  };
  rollback: "unmountRestoreFstabAndClearNewFilesystemSignatures";
  applied?: boolean;
  verified?: boolean;
  filesystem?: OmvBtrfsFilesystem & {
    label: string;
    type: "btrfs";
    devices: string[];
  };
};

export type OmvBtrfsScan = {
  kind: "scrub";
  state: "idle" | "inProgress" | "completed" | "failed";
  progressPercent: number | null;
  errors: number | null;
};

export type OmvBtrfsMaintenanceFilesystem = OmvBtrfsFilesystem & {
  devicefile: string;
  level: string;
  status: "healthy" | "warning" | "degraded" | string;
  totalDevices: number;
  activeDevices: number;
  missingDevices: number;
  operation: string | null;
  operationPercent: number | null;
  deviceErrors: Record<string, number>;
  deviceErrorCount: number;
  kind: "btrfs";
};

export type OmvBtrfsMaintenance = {
  filesystem: OmvBtrfsMaintenanceFilesystem;
  scan: OmvBtrfsScan;
  canStartScrub: boolean;
};

export type OmvBtrfsScrubDesiredState = {
  schema: "echo.omv.btrfs-scrub-desired.v1";
  filesystemUuid: string;
  operation: "start";
};

export type OmvBtrfsScrubPlan = {
  schema: "echo.omv.btrfs-scrub-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "start";
  requiresApproval: true;
  desired: OmvBtrfsScrubDesiredState;
  filesystem: OmvBtrfsMaintenanceFilesystem;
  before: OmvBtrfsScan;
  safety: {
    scope: "echoManagedMountedBtrfsRaid1Only";
    data: "checksummedReplicasMayBeReadAndRepaired";
    activeMaintenance: "mustBeAbsent";
    ioLoad: "high";
    wait: false;
    readOnly: false;
    force: false;
    cancel: false;
    rollback: "noneAfterScrubAccepted";
  };
  applied?: boolean;
  verified?: boolean;
  maintenanceState?: "scrubbing" | "completed" | "completedWithErrors";
  scan?: OmvBtrfsScan;
};

export type OmvBtrfsReplacementMember = {
  devid: number;
  sizeBytes: number;
  usedBytes: number;
  devicefile: string | null;
  missing: boolean;
};

export type OmvBtrfsReplacementCandidate = {
  filesystem: OmvBtrfsMaintenanceFilesystem;
  missingMember: OmvBtrfsReplacementMember & {
    devicefile: null;
    missing: true;
  };
  survivingMember: OmvBtrfsReplacementMember &
    OmvBtrfsRaid1Candidate & {
      missing: false;
      replaceTarget: false;
      writeable: true;
      errorStats: Record<string, number>;
      errorCount: 0;
    };
  minimumReplacementBytes: number;
  replacementDevices: OmvBtrfsRaid1Candidate[];
};

export type OmvBtrfsReplaceDesiredState = {
  schema: "echo.omv.btrfs-replace-desired.v1";
  filesystemUuid: string;
  missingDevid: number;
  replacementDevice: string;
  dataPreserved: true;
};

export type OmvBtrfsReplaceStatus = {
  kind: "deviceReplace";
  state: "idle" | "inProgress" | "completed" | "failed";
  progressPercent: number | null;
  errors: number | null;
};

export type OmvBtrfsReplacePlan = {
  schema: "echo.omv.btrfs-replace-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "replaceMissingMember";
  requiresApproval: true;
  desired: OmvBtrfsReplaceDesiredState;
  filesystem: OmvBtrfsMaintenanceFilesystem;
  missingMember: OmvBtrfsReplacementCandidate["missingMember"];
  survivingMember: OmvBtrfsReplacementCandidate["survivingMember"];
  replacement: OmvBtrfsRaid1Candidate;
  minimumReplacementBytes: number;
  before: OmvBtrfsReplaceStatus;
  applied?: boolean;
  verified?: boolean;
  dataPreserved?: true;
  maintenanceState?:
    | "replacing"
    | "acceptedOrCompleted"
    | "completedWithErrors";
  replacementStatus?: OmvBtrfsReplaceStatus;
};

export type OmvMdRaid1ReplacementMember = {
  devicefile: string;
  slot: number | null;
  states: string[];
  sizeBytes?: number;
  serial?: string | null;
  wwn?: string | null;
};

export type OmvMdRaid1ReplacementCandidate = {
  array: {
    name: string;
    devicefile: string;
    uuid: string;
  };
  survivingMember: OmvMdRaid1ReplacementMember & { sizeBytes: number };
  failedMember: OmvMdRaid1ReplacementMember | null;
  missingSlot: 0 | 1;
  minimumReplacementBytes: number;
  replacementDevices: OmvMdRaid1Candidate[];
};

export type OmvMdRaid1ReplaceDesiredState = {
  schema: "echo.omv.mdraid1-replace-desired.v1";
  name: string;
  arrayUuid: string;
  replacementDevice: string;
  dataPreserved: true;
};

export type OmvMdRaid1ReplacePlan = {
  schema: "echo.omv.mdraid1-replace-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "replaceFailedMember";
  requiresApproval: true;
  desired: OmvMdRaid1ReplaceDesiredState;
  array: OmvMdRaid1ReplacementCandidate["array"];
  survivingMember: OmvMdRaid1ReplacementCandidate["survivingMember"];
  failedMember: OmvMdRaid1ReplacementCandidate["failedMember"];
  missingSlot: 0 | 1;
  replacement: OmvMdRaid1Candidate;
  minimumReplacementBytes: number;
  applied?: boolean;
  verified?: boolean;
  dataPreserved?: true;
  maintenanceState?: "recovering" | "acceptedOrCompleted";
};

export type OmvExt4VolumeDesiredState = {
  schema: "echo.omv.ext4-volume-desired.v1";
  arrayUuid: string;
  name: string;
  dataLossConfirmed: true;
};

export type OmvExt4VolumePlan = {
  schema: "echo.omv.ext4-volume-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "createAndMount";
  requiresApproval: true;
  desired: OmvExt4VolumeDesiredState;
  array: OmvManagedMdRaid1;
  mountpoint: string;
  safety: {
    destructive: true;
    dataLossConfirmed: true;
    source: "healthyBlankEchoManagedMdRaid1Only";
    filesystem: "ext4Only";
    mountRoot: string;
    persistentIdentity: "filesystemUuid";
    force: false;
  };
  applied?: boolean;
  verified?: boolean;
  filesystem?: {
    uuid: string;
    label: string;
    type: "ext4";
    devicefile: string;
    mountpoint: string;
    readOnly: false;
  };
};

export type OmvZfsPool = {
  name: string;
  poolGuid: string;
  health: "ONLINE" | string;
  sizeBytes: number;
  rootMountpoint: string;
  datasetCount: number;
  mountedCount: number;
  safeToExport: true;
};

export type OmvZfsImportCandidate = {
  name: string;
  poolGuid: string;
  state: "ONLINE" | string;
  layout: "mirror" | "raidz1" | "raidz2" | "raidz3" | "stripe";
  configHash: string;
  safeToImport: true;
};

export type OmvZfsMirrorReplacementCandidate = {
  pool: {
    name: string;
    poolGuid: string;
    health: "DEGRADED" | "ONLINE" | string;
    sizeBytes: number;
  };
  layout: "twoDiskMirror";
  replaceableMember: {
    slot: number;
    vdevGuid: string;
    state: "DEGRADED" | "FAULTED" | "OFFLINE" | "REMOVED" | "UNAVAIL";
  };
  minimumReplacementBytes: number;
  replacementDevices: OmvZfsMirrorCandidate[];
};

export type OmvZfsMirrorReplaceDesiredState = {
  schema: "echo.omv.zfs-mirror-replace-desired.v1";
  name: string;
  poolGuid: string;
  oldVdevGuid: string;
  replacementDevice: string;
  dataPreserved: true;
};

export type OmvZfsMirrorReplacePlan = {
  schema: "echo.omv.zfs-mirror-replace-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "replace";
  requiresApproval: true;
  desired: OmvZfsMirrorReplaceDesiredState;
  pool: OmvZfsMirrorReplacementCandidate["pool"];
  failedMember: OmvZfsMirrorReplacementCandidate["replaceableMember"];
  replacement: OmvZfsMirrorCandidate;
  minimumReplacementBytes: number;
  safety: {
    data: "preservedDuringResilver";
    scope: "singleTwoDiskMirrorOnly";
    target: "failedLeafVdevGuid";
    replacement: "wholeBlankNonRemovableWithPersistentIdentity";
    minimumSize: "onlineSiblingDeviceSize";
    force: false;
    sequentialReconstruction: false;
    wait: false;
    activeMaintenance: "mustBeAbsent";
    rollback: "noneAfterReplacementAccepted";
  };
  applied?: boolean;
  verified?: boolean;
  dataPreserved?: true;
  maintenanceState?: "resilvering" | "acceptedOrCompleted";
};

export type OmvZfsPoolExportDesiredState = {
  schema: "echo.omv.zfs-pool-export-desired.v1";
  name: string;
  poolGuid: string;
  dataPreserved: true;
};

export type OmvZfsPoolImportDesiredState = {
  schema: "echo.omv.zfs-pool-import-desired.v1";
  name: string;
  poolGuid: string;
  mountPolicy: "echoDataRootOnly";
};

export type OmvZfsPoolExportPlan = {
  schema: "echo.omv.zfs-pool-export-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "export";
  requiresApproval: true;
  desired: OmvZfsPoolExportDesiredState;
  pool: {
    name: string;
    poolGuid: string;
    health: "ONLINE" | string;
    sizeBytes: number;
    availability?: "exported";
  };
  datasetCount: number;
  mountedCount: number;
  safety: {
    data: "preserved";
    force: false;
    poolState: "onlineOnly";
    mounts: "echoDataRootOnly";
    dependentShares: "mustBeDetached";
    activeMaintenance: "mustBeAbsent";
    rollback: "notAttemptedAfterConfirmedExport";
  };
  applied?: boolean;
  verified?: boolean;
  dataPreserved?: true;
};

export type OmvZfsPoolImportPlan = {
  schema: "echo.omv.zfs-pool-import-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "import";
  requiresApproval: true;
  desired: OmvZfsPoolImportDesiredState;
  candidate: OmvZfsImportCandidate;
  safety: {
    data: "preserved";
    identity: "guidBound";
    force: false;
    recoveryFlags: false;
    destroyedPools: false;
    inspection: "readOnlyNoMountBeforeWritableImport";
    mounts: "echoDataRootOnly";
    encryption: "notYetSupported";
    rollback: "exportOnFailure";
  };
  applied?: boolean;
  verified?: boolean;
  dataPreserved?: true;
  pool?: {
    name: string;
    poolGuid: string;
    health: "ONLINE" | string;
    sizeBytes: number;
    availability: "imported";
    datasetCount: number;
    mountedCount: number;
  };
};

export type OmvZfsScan = {
  kind: "none" | "scrub" | "resilver" | "unknown";
  state: "idle" | "inProgress" | "completed" | "unknown";
  progressPercent: number | null;
  errors: number | null;
  summaryHash: string;
};

export type OmvZfsMaintenancePool = {
  pool: {
    name: string;
    poolGuid: string;
    health: "ONLINE" | string;
    sizeBytes: number;
  };
  rootMountpoint: string;
  scan: OmvZfsScan;
  canStartScrub: boolean;
};

export type OmvZfsScrubDesiredState = {
  schema: "echo.omv.zfs-scrub-desired.v1";
  name: string;
  poolGuid: string;
  operation: "start";
};

export type OmvZfsScrubPlan = {
  schema: "echo.omv.zfs-scrub-plan.v1";
  planId: string;
  baseRevision: string;
  operation: "start";
  requiresApproval: true;
  desired: OmvZfsScrubDesiredState;
  pool: OmvZfsMaintenancePool["pool"];
  before: OmvZfsScan;
  safety: {
    data: "checksummedAndRepairableReplicasMayBeRepaired";
    poolState: "onlineOnly";
    mounts: "echoDataRootOnly";
    activeMaintenance: "mustBeAbsent";
    ioLoad: "high";
    wait: false;
    pause: false;
    stop: false;
    rollback: "noneAfterScrubAccepted";
  };
  applied?: boolean;
  verified?: boolean;
  maintenanceState?: "scrubbing" | "completed";
  scan?: OmvZfsScan;
};

export type OmvUpsDevice = {
  name: string;
  available: boolean;
  state:
    | "online"
    | "onBattery"
    | "lowBattery"
    | "replaceBattery"
    | "shutdownPending"
    | "offline"
    | "unknown";
  statusFlags: string[];
  chargePercent: number | null;
  runtimeSeconds: number | null;
  loadPercent: number | null;
  inputVoltage: number | null;
  outputVoltage: number | null;
  batteryVoltage: number | null;
  temperatureC: number | null;
  manufacturer: string | null;
  model: string | null;
};

export type OmvUpsSnapshot = {
  schemaVersion: 1;
  source: "nut";
  readOnly: true;
  configured: boolean;
  available: boolean;
  state: "ready" | "degraded" | "unavailable" | "notConfigured";
  code:
    | "toolMissing"
    | "serviceUnavailable"
    | "emptyInventory"
    | "partialRead"
    | "deviceUnavailable"
    | null;
  devices: OmvUpsDevice[];
};

export type OmvUpsShutdownPolicyDesiredState = {
  schema: "echo.ups-shutdown-policy-desired.v1";
  enabled: boolean;
  requiredConsecutiveSamples: number;
};

export type OmvUpsShutdownPolicy = {
  schemaVersion: 1;
  enabled: boolean;
  requiredConsecutiveSamples: number;
  configured?: boolean;
  source?: "localPolicy";
  shutdownTrigger: "FSD or persistent OB+LB";
};

export type OmvUpsShutdownPolicyPlan = {
  schema: "echo.ups-shutdown-policy-desired.v1";
  planId: string;
  operation: "none" | "enable" | "disable" | "update";
  requiresApproval: boolean;
  configured: boolean;
  current: Omit<
    OmvUpsShutdownPolicy,
    "configured" | "source" | "shutdownTrigger"
  >;
  desired: Omit<
    OmvUpsShutdownPolicy,
    "configured" | "source" | "shutdownTrigger"
  >;
  shutdownTrigger: "FSD or persistent OB+LB";
  applied?: boolean;
  verified?: boolean;
};

export type OmvSmartSelfTestDesiredState = {
  schema: "echo.omv.smart-self-test-desired.v1";
  devicefile: string;
  test: "short" | "long";
};

export type OmvSmartSelfTestStatus = {
  devicefile: string;
  model: string | null;
  identityHash: string;
  supported: boolean;
  state: "idle" | "inProgress" | "unknown";
  kind: "short" | "long" | "unknown" | null;
  progressPercent: number | null;
  readOnly: true;
  source: "smartctl";
};

export type OmvSmartSelfTestPlan = {
  schema: "echo.omv.smart-self-test-plan.v1";
  planId: string;
  operation: "start";
  requiresApproval: true;
  desired: OmvSmartSelfTestDesiredState;
  identityHash: string;
  before: Pick<OmvSmartSelfTestStatus, "state" | "kind" | "progressPercent">;
  safety: {
    target: "enumeratedWholeDisk";
    allowedTests: ["short", "long"];
    activeTest: "mustBeAbsent";
    captive: false;
    abort: false;
  };
  applied?: boolean;
  verified?: boolean;
  selfTest?: OmvSmartSelfTestStatus;
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

/* ------------------------------------------------------------------ *
 * 原生存储面:不经 OMV,直接读内核 / zpool / smartctl。
 * 载荷与 OMV 桥完全同构,页面可无缝切换数据源。
 * ------------------------------------------------------------------ */

const NATIVE = "/api/appliance/storage";

export type StorageSource = "omv" | "native";

export function fetchNativeStatus(): Promise<OmvStatus> {
  return readJson(`${NATIVE}/status`, "无法读取原生存储接入状态");
}

export async function fetchNativeFilesystems(): Promise<OmvFilesystem[]> {
  const result = await readJson<{ filesystems: OmvFilesystem[] }>(
    `${NATIVE}/filesystems`,
    "无法读取存储卷",
  );
  return result.filesystems;
}

export async function fetchNativeSmart(devicefile: string): Promise<OmvSmart> {
  const result = await readJson<{ smart: OmvSmart }>(
    `${NATIVE}/smart?devicefile=${encodeURIComponent(devicefile)}`,
    "无法读取磁盘 SMART 状态",
  );
  return result.smart;
}

export async function fetchNativeSmartDevices(): Promise<OmvSmartDevice[]> {
  const result = await readJson<{ devices: OmvSmartDevice[] }>(
    `${NATIVE}/smart/devices`,
    "无法读取物理磁盘",
  );
  return result.devices;
}

export async function fetchNativeStorageTopology(): Promise<OmvStorageTopology> {
  return readJson<OmvStorageTopology>(
    `${NATIVE}/topology`,
    "无法读取磁盘、阵列与逻辑卷关系",
  );
}

export function fetchNativeHealth(): Promise<OmvHealthSnapshot> {
  return readJson<OmvHealthSnapshot>(
    `${NATIVE}/health`,
    "无法读取原生存储健康状态",
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
    "无法生成共享文件夹预览",
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
    "无法应用共享文件夹变更",
    approvalHeader(approvalToken),
  );
}

export function planOmvSharedFolderRename(
  desired: OmvSharedFolderRenameDesiredState,
): Promise<OmvSharedFolderRenamePlan> {
  return postJson(
    "/api/appliance/omv/sharing/folders/rename/plan",
    desired,
    "无法生成共享文件夹重命名预览",
  );
}

export function applyOmvSharedFolderRename(
  desired: OmvSharedFolderRenameDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvSharedFolderRenamePlan> {
  return postJson(
    "/api/appliance/omv/sharing/folders/rename/apply",
    { desired, planId },
    "无法重命名共享文件夹",
    approvalHeader(approvalToken),
  );
}

export function planOmvSharedFolderDetach(
  desired: OmvSharedFolderDetachDesiredState,
): Promise<OmvSharedFolderDetachPlan> {
  return postJson(
    "/api/appliance/omv/sharing/folders/detach/plan",
    desired,
    "无法生成共享文件夹解除登记预览",
  );
}

export function applyOmvSharedFolderDetach(
  desired: OmvSharedFolderDetachDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvSharedFolderDetachPlan> {
  return postJson(
    "/api/appliance/omv/sharing/folders/detach/apply",
    { desired, planId },
    "无法解除共享文件夹登记",
    approvalHeader(approvalToken),
  );
}

export function planOmvSharedFolderDelete(
  desired: OmvSharedFolderDeleteDesiredState,
): Promise<OmvSharedFolderDeletePlan> {
  return postJson(
    "/api/appliance/omv/sharing/folders/delete/plan",
    desired,
    "无法生成空目录删除预览",
  );
}

export function applyOmvSharedFolderDelete(
  desired: OmvSharedFolderDeleteDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvSharedFolderDeletePlan> {
  return postJson(
    "/api/appliance/omv/sharing/folders/delete/apply",
    { desired, planId },
    "无法删除空共享文件夹",
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

export function planOmvNfsShareRemove(
  desired: OmvNfsRemoveDesiredState,
): Promise<OmvNfsRemovePlan> {
  return postJson(
    "/api/appliance/omv/sharing/nfs/remove/plan",
    desired,
    "无法生成 NFS 规则移除预览",
  );
}

export function applyOmvNfsShareRemove(
  desired: OmvNfsRemoveDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvNfsRemovePlan> {
  return postJson(
    "/api/appliance/omv/sharing/nfs/remove/apply",
    { desired, planId },
    "无法移除 NFS 规则",
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

export async function fetchOmvZfsMirrorCandidates(): Promise<
  OmvZfsMirrorCandidate[]
> {
  const result = await readJson<{ devices: OmvZfsMirrorCandidate[] }>(
    "/api/appliance/omv/pools/zfs-mirror/candidates",
    "无法读取 ZFS 镜像候选磁盘",
  );
  return result.devices;
}

export function planOmvZfsMirror(
  desired: OmvZfsMirrorDesiredState,
): Promise<OmvZfsMirrorPlan> {
  return postJson(
    "/api/appliance/omv/pools/zfs-mirror/plan",
    desired,
    "无法生成 ZFS 镜像创建预览",
  );
}

export function applyOmvZfsMirror(
  desired: OmvZfsMirrorDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvZfsMirrorPlan> {
  return postJson(
    "/api/appliance/omv/pools/zfs-mirror/apply",
    { desired, planId },
    "无法创建 ZFS 镜像存储池",
    approvalHeader(approvalToken),
  );
}

export async function fetchOmvMdRaid1Candidates(): Promise<
  OmvMdRaid1Candidate[]
> {
  const result = await readJson<{ devices: OmvMdRaid1Candidate[] }>(
    "/api/appliance/omv/arrays/mdraid1/candidates",
    "无法读取 Linux RAID1 候选磁盘",
  );
  return result.devices;
}

export function planOmvMdRaid1(
  desired: OmvMdRaid1DesiredState,
): Promise<OmvMdRaid1Plan> {
  return postJson(
    "/api/appliance/omv/arrays/mdraid1/plan",
    desired,
    "无法生成 Linux RAID1 创建预览",
  );
}

export function applyOmvMdRaid1(
  desired: OmvMdRaid1DesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvMdRaid1Plan> {
  return postJson(
    "/api/appliance/omv/arrays/mdraid1/apply",
    { desired, planId },
    "无法创建 Linux RAID1 阵列",
    approvalHeader(approvalToken),
  );
}

export async function fetchOmvBtrfsRaid1Candidates(): Promise<
  OmvBtrfsRaid1Candidate[]
> {
  const result = await readJson<{ devices: OmvBtrfsRaid1Candidate[] }>(
    "/api/appliance/omv/volumes/btrfs-raid1/candidates",
    "无法读取 Btrfs RAID1 候选磁盘",
  );
  return result.devices;
}

export function planOmvBtrfsRaid1(
  desired: OmvBtrfsRaid1DesiredState,
): Promise<OmvBtrfsRaid1Plan> {
  return postJson(
    "/api/appliance/omv/volumes/btrfs-raid1/plan",
    desired,
    "无法生成 Btrfs RAID1 创建预览",
  );
}

export function applyOmvBtrfsRaid1(
  desired: OmvBtrfsRaid1DesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvBtrfsRaid1Plan> {
  return postJson(
    "/api/appliance/omv/volumes/btrfs-raid1/apply",
    { desired, planId },
    "无法创建 Btrfs RAID1 卷",
    approvalHeader(approvalToken),
  );
}

export async function fetchOmvBtrfsMaintenance(): Promise<
  OmvBtrfsMaintenance[]
> {
  const result = await readJson<{ filesystems: OmvBtrfsMaintenance[] }>(
    "/api/appliance/omv/volumes/btrfs-raid1/maintenance",
    "无法读取 Btrfs scrub 状态",
  );
  return result.filesystems;
}

export function planOmvBtrfsScrub(
  desired: OmvBtrfsScrubDesiredState,
): Promise<OmvBtrfsScrubPlan> {
  return postJson(
    "/api/appliance/omv/volumes/btrfs-raid1/scrub/plan",
    desired,
    "无法生成 Btrfs scrub 预览",
  );
}

export function applyOmvBtrfsScrub(
  desired: OmvBtrfsScrubDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvBtrfsScrubPlan> {
  return postJson(
    "/api/appliance/omv/volumes/btrfs-raid1/scrub/apply",
    { desired, planId },
    "无法启动 Btrfs scrub",
    approvalHeader(approvalToken),
  );
}

export async function fetchOmvBtrfsReplacementCandidates(): Promise<
  OmvBtrfsReplacementCandidate[]
> {
  const result = await readJson<{
    replacements: OmvBtrfsReplacementCandidate[];
  }>(
    "/api/appliance/omv/volumes/btrfs-raid1/replacement-candidates",
    "无法读取 Btrfs RAID1 换盘候选",
  );
  return result.replacements;
}

export function planOmvBtrfsReplace(
  desired: OmvBtrfsReplaceDesiredState,
): Promise<OmvBtrfsReplacePlan> {
  return postJson(
    "/api/appliance/omv/volumes/btrfs-raid1/replace/plan",
    desired,
    "无法生成 Btrfs RAID1 换盘预览",
  );
}

export function applyOmvBtrfsReplace(
  desired: OmvBtrfsReplaceDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvBtrfsReplacePlan> {
  return postJson(
    "/api/appliance/omv/volumes/btrfs-raid1/replace/apply",
    { desired, planId },
    "无法启动 Btrfs RAID1 换盘",
    approvalHeader(approvalToken),
  );
}

export async function fetchOmvMdRaid1ReplacementCandidates(): Promise<
  OmvMdRaid1ReplacementCandidate[]
> {
  const result = await readJson<{
    replacements: OmvMdRaid1ReplacementCandidate[];
  }>(
    "/api/appliance/omv/arrays/mdraid1/replacement-candidates",
    "无法读取 Linux RAID1 换盘候选",
  );
  return result.replacements;
}

export function planOmvMdRaid1Replace(
  desired: OmvMdRaid1ReplaceDesiredState,
): Promise<OmvMdRaid1ReplacePlan> {
  return postJson(
    "/api/appliance/omv/arrays/mdraid1/replace/plan",
    desired,
    "无法生成 Linux RAID1 换盘预览",
  );
}

export function applyOmvMdRaid1Replace(
  desired: OmvMdRaid1ReplaceDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvMdRaid1ReplacePlan> {
  return postJson(
    "/api/appliance/omv/arrays/mdraid1/replace/apply",
    { desired, planId },
    "无法启动 Linux RAID1 换盘重建",
    approvalHeader(approvalToken),
  );
}

export async function fetchOmvExt4VolumeCandidates(): Promise<
  OmvManagedMdRaid1[]
> {
  const result = await readJson<{
    arrays: OmvManagedMdRaid1[];
  }>("/api/appliance/omv/volumes/ext4/candidates", "无法读取 EXT4 候选阵列");
  return result.arrays;
}

export function planOmvExt4Volume(
  desired: OmvExt4VolumeDesiredState,
): Promise<OmvExt4VolumePlan> {
  return postJson(
    "/api/appliance/omv/volumes/ext4/plan",
    desired,
    "无法生成 EXT4 卷创建预览",
  );
}

export function applyOmvExt4Volume(
  desired: OmvExt4VolumeDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvExt4VolumePlan> {
  return postJson(
    "/api/appliance/omv/volumes/ext4/apply",
    { desired, planId },
    "无法创建并挂载 EXT4 卷",
    approvalHeader(approvalToken),
  );
}

export async function fetchOmvZfsPools(): Promise<OmvZfsPool[]> {
  const result = await readJson<{ pools: OmvZfsPool[] }>(
    "/api/appliance/omv/pools/zfs",
    "无法读取可安全导出的 ZFS 存储池",
  );
  return result.pools;
}

export async function fetchOmvZfsMirrorReplacementCandidates(): Promise<
  OmvZfsMirrorReplacementCandidate[]
> {
  const result = await readJson<{
    replacements: OmvZfsMirrorReplacementCandidate[];
  }>(
    "/api/appliance/omv/pools/zfs-mirror/replacement-candidates",
    "无法读取 ZFS 镜像换盘候选",
  );
  return result.replacements;
}

export function planOmvZfsMirrorReplace(
  desired: OmvZfsMirrorReplaceDesiredState,
): Promise<OmvZfsMirrorReplacePlan> {
  return postJson(
    "/api/appliance/omv/pools/zfs-mirror/replace/plan",
    desired,
    "无法生成 ZFS 镜像换盘预览",
  );
}

export function applyOmvZfsMirrorReplace(
  desired: OmvZfsMirrorReplaceDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvZfsMirrorReplacePlan> {
  return postJson(
    "/api/appliance/omv/pools/zfs-mirror/replace/apply",
    { desired, planId },
    "无法启动 ZFS 镜像换盘",
    approvalHeader(approvalToken),
  );
}

export async function fetchOmvZfsImportCandidates(): Promise<
  OmvZfsImportCandidate[]
> {
  const result = await readJson<{ pools: OmvZfsImportCandidate[] }>(
    "/api/appliance/omv/pools/zfs/import-candidates",
    "无法读取可安全导入的 ZFS 存储池",
  );
  return result.pools;
}

export function planOmvZfsPoolExport(
  desired: OmvZfsPoolExportDesiredState,
): Promise<OmvZfsPoolExportPlan> {
  return postJson(
    "/api/appliance/omv/pools/zfs/export/plan",
    desired,
    "无法生成 ZFS 存储池导出预览",
  );
}

export function applyOmvZfsPoolExport(
  desired: OmvZfsPoolExportDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvZfsPoolExportPlan> {
  return postJson(
    "/api/appliance/omv/pools/zfs/export/apply",
    { desired, planId },
    "无法安全导出 ZFS 存储池",
    approvalHeader(approvalToken),
  );
}

export function planOmvZfsPoolImport(
  desired: OmvZfsPoolImportDesiredState,
): Promise<OmvZfsPoolImportPlan> {
  return postJson(
    "/api/appliance/omv/pools/zfs/import/plan",
    desired,
    "无法生成 ZFS 存储池导入预览",
  );
}

export function applyOmvZfsPoolImport(
  desired: OmvZfsPoolImportDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvZfsPoolImportPlan> {
  return postJson(
    "/api/appliance/omv/pools/zfs/import/apply",
    { desired, planId },
    "无法安全导入 ZFS 存储池",
    approvalHeader(approvalToken),
  );
}

export async function fetchOmvZfsMaintenance(): Promise<
  OmvZfsMaintenancePool[]
> {
  const result = await readJson<{ pools: OmvZfsMaintenancePool[] }>(
    "/api/appliance/omv/pools/zfs/maintenance",
    "无法读取 ZFS 校验与重建状态",
  );
  return result.pools;
}

export function planOmvZfsScrub(
  desired: OmvZfsScrubDesiredState,
): Promise<OmvZfsScrubPlan> {
  return postJson(
    "/api/appliance/omv/pools/zfs/scrub/plan",
    desired,
    "无法生成 ZFS 校验预览",
  );
}

export function applyOmvZfsScrub(
  desired: OmvZfsScrubDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvZfsScrubPlan> {
  return postJson(
    "/api/appliance/omv/pools/zfs/scrub/apply",
    { desired, planId },
    "无法启动 ZFS 校验",
    approvalHeader(approvalToken),
  );
}

export function fetchOmvUpsStatus(): Promise<OmvUpsSnapshot> {
  return readJson("/api/appliance/omv/power/ups", "无法读取 UPS 电源状态");
}

export function fetchOmvUpsShutdownPolicy(): Promise<OmvUpsShutdownPolicy> {
  return readJson(
    "/api/appliance/omv/power/ups/shutdown-policy",
    "无法读取 UPS 自动关机策略",
  );
}

export function planOmvUpsShutdownPolicy(
  desired: OmvUpsShutdownPolicyDesiredState,
): Promise<OmvUpsShutdownPolicyPlan> {
  return postJson(
    "/api/appliance/omv/power/ups/shutdown-policy/plan",
    desired,
    "无法生成 UPS 自动关机策略预览",
  );
}

export function applyOmvUpsShutdownPolicy(
  desired: OmvUpsShutdownPolicyDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvUpsShutdownPolicyPlan> {
  return postJson(
    "/api/appliance/omv/power/ups/shutdown-policy/apply",
    { desired, planId },
    "无法更新 UPS 自动关机策略",
    approvalHeader(approvalToken),
  );
}

export function fetchOmvSmartSelfTest(
  devicefile: string,
): Promise<OmvSmartSelfTestStatus> {
  return readJson(
    `/api/appliance/omv/smart/self-test?devicefile=${encodeURIComponent(devicefile)}`,
    "无法读取 SMART 自检状态",
  );
}

export function planOmvSmartSelfTest(
  desired: OmvSmartSelfTestDesiredState,
): Promise<OmvSmartSelfTestPlan> {
  return postJson(
    "/api/appliance/omv/smart/self-test/plan",
    desired,
    "无法生成 SMART 自检预览",
  );
}

export function applyOmvSmartSelfTest(
  desired: OmvSmartSelfTestDesiredState,
  planId: string,
  approvalToken: string,
): Promise<OmvSmartSelfTestPlan> {
  return postJson(
    "/api/appliance/omv/smart/self-test/apply",
    { desired, planId },
    "无法启动 SMART 自检",
    approvalHeader(approvalToken),
  );
}
