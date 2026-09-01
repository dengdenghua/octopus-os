import { useEffect, useState } from "react";
import {
  ExternalLinkIcon,
  FolderKeyIcon,
  FolderPlusIcon,
  FoldersIcon,
  GaugeIcon,
  KeyRoundIcon,
  Loader2Icon,
  RefreshCwIcon,
  ServerIcon,
  ShieldCheckIcon,
  SlidersHorizontalIcon,
  UserPlusIcon,
  UserRoundIcon,
  UsersRoundIcon,
} from "lucide-react";

import { requestHighRiskApproval } from "@/appliance/approval";
import {
  applyEchoAccountLink,
  applyEchoAccountPassword,
  applyEchoAccountStatus,
  applyEchoAccountUnlink,
  fetchEchoAccounts,
  planEchoAccountLink,
  planEchoAccountPassword,
  planEchoAccountStatus,
  planEchoAccountUnlink,
  type EchoAccountDirectory,
  type EchoAccountLinkDesired,
  type EchoAccountLinkPlan,
  type EchoAccountLifecyclePlan,
  type EchoAccountPasswordDesired,
  type EchoAccountStatusDesired,
  type EchoAccountUnlinkDesired,
} from "@/appliance/accounts";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";
import {
  applyOmvGroup,
  applyOmvUser,
  applyOmvUserPassword,
  applyOmvFilesystemQuota,
  applyOmvNfsShare,
  applyOmvSharedFolder,
  applyOmvSharePrivilege,
  applyOmvSmbShare,
  fetchOmvFilesystems,
  fetchOmvSharePrivileges,
  fetchOmvSharingOverview,
  fetchOmvStatus,
  planOmvFilesystemQuota,
  planOmvGroup,
  planOmvNfsShare,
  planOmvSharedFolder,
  planOmvSharePrivilege,
  planOmvSmbShare,
  planOmvUser,
  planOmvUserPassword,
  type OmvFilesystem,
  type OmvGroupDesiredState,
  type OmvGroupPlan,
  type OmvNfsDesiredState,
  type OmvNfsPlan,
  type OmvQuotaDesiredState,
  type OmvQuotaPlan,
  type OmvSharedFolder,
  type OmvSharedFolderDesiredState,
  type OmvSharedFolderPlan,
  type OmvSharePrivilege,
  type OmvSharePrivilegeDesiredState,
  type OmvSharePrivilegePlan,
  type OmvSharingOverview,
  type OmvStatus,
  type OmvSmbDesiredState,
  type OmvSmbPlan,
  type OmvUserDesiredState,
  type OmvUserPlan,
  type OmvUserPasswordDesiredState,
  type OmvUserPasswordPlan,
} from "@/appliance/omv";

const GIB_BYTES = 1024 ** 3;
const MAX_SAFE_QUOTA_GIB = Math.floor(Number.MAX_SAFE_INTEGER / GIB_BYTES);
const PORTABLE_SHARE_NAME = /^(?!\.)(?!.*\.$)[A-Za-z0-9._-]{1,64}$/;
const ACCOUNT_NAME = /^[a-z][a-z0-9_-]{0,31}$/;
const RESERVED_ACCOUNT_NAMES = new Set([
  "adm",
  "admin",
  "daemon",
  "docker",
  "echo",
  "echo-omv",
  "nobody",
  "openmediavault",
  "root",
  "ssh",
  "sudo",
  "users",
  "www-data",
]);

function validFamilyAccountName(value: string) {
  return (
    ACCOUNT_NAME.test(value) &&
    !RESERVED_ACCOUNT_NAMES.has(value) &&
    !value.startsWith("echo-")
  );
}

const permissionLabel: Record<OmvSharePrivilege["permission"], string> = {
  inherit: "未单独设置",
  none: "禁止访问",
  read: "只读",
  readWrite: "读写",
};

const smbFieldLabel: Record<string, string> = {
  enabled: "启用规则",
  readOnly: "只读访问",
  browseable: "网络中可发现",
  recycleBin: "回收站",
  comment: "备注",
};

const smbBooleanFields: Array<{
  field: "enabled" | "readOnly" | "browseable" | "recycleBin";
  label: string;
}> = [
  { field: "enabled", label: "启用规则" },
  { field: "readOnly", label: "只读访问" },
  { field: "browseable", label: "网络中可发现" },
  { field: "recycleBin", label: "启用回收站" },
];

const nfsFieldLabel: Record<string, string> = {
  readOnly: "只读访问",
  comment: "备注",
};

function planValue(value: boolean | string | null) {
  if (value === null) return "未配置";
  if (typeof value === "boolean") return value ? "开启" : "关闭";
  return value || "空";
}

function quotaLimitLabel(bytes: number) {
  if (bytes === 0) return "不限制";
  if (bytes % GIB_BYTES === 0) return `${bytes / GIB_BYTES} GiB`;
  return `${bytes.toLocaleString("zh-CN")} 字节`;
}

export function OmvSharingPanel() {
  const [reloadKey, setReloadKey] = useState(0);
  const [status, setStatus] = useState<OmvStatus | null>(null);
  const [overview, setOverview] = useState<OmvSharingOverview | null>(null);
  const [echoAccounts, setEchoAccounts] = useState<EchoAccountDirectory | null>(
    null,
  );
  const [filesystems, setFilesystems] = useState<OmvFilesystem[]>([]);
  const [privileges, setPrivileges] = useState<
    Record<string, OmvSharePrivilege[]>
  >({});
  const [privilegeLoading, setPrivilegeLoading] = useState<string | null>(null);
  const [editingPrivilegeFolder, setEditingPrivilegeFolder] =
    useState<OmvSharedFolder | null>(null);
  const [privilegeDesired, setPrivilegeDesired] =
    useState<OmvSharePrivilegeDesiredState | null>(null);
  const [privilegePlan, setPrivilegePlan] =
    useState<OmvSharePrivilegePlan | null>(null);
  const [privilegePlanning, setPrivilegePlanning] = useState(false);
  const [privilegeApprovalOpen, setPrivilegeApprovalOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingFolder, setEditingFolder] = useState<OmvSharedFolder | null>(
    null,
  );
  const [desired, setDesired] = useState<OmvSmbDesiredState | null>(null);
  const [plan, setPlan] = useState<OmvSmbPlan | null>(null);
  const [planning, setPlanning] = useState(false);
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [folderCreateOpen, setFolderCreateOpen] = useState(false);
  const [folderMountRef, setFolderMountRef] = useState("");
  const [folderName, setFolderName] = useState("");
  const [folderComment, setFolderComment] = useState("");
  const [folderDesired, setFolderDesired] =
    useState<OmvSharedFolderDesiredState | null>(null);
  const [folderPlan, setFolderPlan] = useState<OmvSharedFolderPlan | null>(
    null,
  );
  const [folderPlanning, setFolderPlanning] = useState(false);
  const [folderApprovalOpen, setFolderApprovalOpen] = useState(false);
  const [editingNfsFolder, setEditingNfsFolder] =
    useState<OmvSharedFolder | null>(null);
  const [nfsDesired, setNfsDesired] = useState<OmvNfsDesiredState | null>(null);
  const [nfsPlan, setNfsPlan] = useState<OmvNfsPlan | null>(null);
  const [nfsPlanning, setNfsPlanning] = useState(false);
  const [nfsApprovalOpen, setNfsApprovalOpen] = useState(false);
  const [quotaFilesystemUuid, setQuotaFilesystemUuid] = useState("");
  const [quotaSubjectType, setQuotaSubjectType] = useState<"user" | "group">(
    "user",
  );
  const [quotaSubjectName, setQuotaSubjectName] = useState("");
  const [quotaLimitGiB, setQuotaLimitGiB] = useState("10");
  const [quotaDesired, setQuotaDesired] = useState<OmvQuotaDesiredState | null>(
    null,
  );
  const [quotaPlan, setQuotaPlan] = useState<OmvQuotaPlan | null>(null);
  const [quotaPlanning, setQuotaPlanning] = useState(false);
  const [quotaApprovalOpen, setQuotaApprovalOpen] = useState(false);
  const [accountMode, setAccountMode] = useState<
    "group" | "user" | "password" | "echo" | "echoPassword" | null
  >(null);
  const [groupName, setGroupName] = useState("");
  const [groupComment, setGroupComment] = useState("");
  const [groupDesired, setGroupDesired] = useState<OmvGroupDesiredState | null>(
    null,
  );
  const [groupPlan, setGroupPlan] = useState<OmvGroupPlan | null>(null);
  const [groupPlanning, setGroupPlanning] = useState(false);
  const [groupApprovalOpen, setGroupApprovalOpen] = useState(false);
  const [userName, setUserName] = useState("");
  const [userDisplayName, setUserDisplayName] = useState("");
  const [userPassword, setUserPassword] = useState("");
  const [userPasswordConfirm, setUserPasswordConfirm] = useState("");
  const [userGroups, setUserGroups] = useState<string[]>([]);
  const [userDesired, setUserDesired] = useState<OmvUserDesiredState | null>(
    null,
  );
  const [userPlan, setUserPlan] = useState<OmvUserPlan | null>(null);
  const [userPlanning, setUserPlanning] = useState(false);
  const [userApprovalOpen, setUserApprovalOpen] = useState(false);
  const [passwordUserName, setPasswordUserName] = useState("");
  const [replacementPassword, setReplacementPassword] = useState("");
  const [replacementPasswordConfirm, setReplacementPasswordConfirm] =
    useState("");
  const [passwordDesired, setPasswordDesired] =
    useState<OmvUserPasswordDesiredState | null>(null);
  const [passwordPlan, setPasswordPlan] = useState<OmvUserPasswordPlan | null>(
    null,
  );
  const [passwordPlanning, setPasswordPlanning] = useState(false);
  const [passwordApprovalOpen, setPasswordApprovalOpen] = useState(false);
  const [echoMemberName, setEchoMemberName] = useState("");
  const [echoDisplayName, setEchoDisplayName] = useState("");
  const [echoPassword, setEchoPassword] = useState("");
  const [echoPasswordConfirm, setEchoPasswordConfirm] = useState("");
  const [echoLinkPlan, setEchoLinkPlan] = useState<EchoAccountLinkPlan | null>(
    null,
  );
  const [echoLinkPlanning, setEchoLinkPlanning] = useState(false);
  const [echoLinkApprovalOpen, setEchoLinkApprovalOpen] = useState(false);
  const [echoLifecycleMember, setEchoLifecycleMember] = useState("");
  const [echoLifecyclePlan, setEchoLifecyclePlan] =
    useState<EchoAccountLifecyclePlan | null>(null);
  const [echoStatusDesired, setEchoStatusDesired] =
    useState<EchoAccountStatusDesired | null>(null);
  const [echoStatusApprovalOpen, setEchoStatusApprovalOpen] = useState(false);
  const [echoUnlinkDesired, setEchoUnlinkDesired] =
    useState<EchoAccountUnlinkDesired | null>(null);
  const [echoUnlinkApprovalOpen, setEchoUnlinkApprovalOpen] = useState(false);
  const [echoReplacementPassword, setEchoReplacementPassword] = useState("");
  const [echoReplacementConfirm, setEchoReplacementConfirm] = useState("");
  const [echoPasswordDesired, setEchoPasswordDesired] =
    useState<EchoAccountPasswordDesired | null>(null);
  const [echoPasswordApprovalOpen, setEchoPasswordApprovalOpen] =
    useState(false);
  const [echoLifecyclePlanning, setEchoLifecyclePlanning] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    setPrivileges({});
    setEditingPrivilegeFolder(null);
    setPrivilegeDesired(null);
    setPrivilegePlan(null);
    setQuotaPlan(null);
    setQuotaDesired(null);
    setNfsPlan(null);
    setFolderPlan(null);
    setFolderDesired(null);
    setGroupPlan(null);
    setGroupDesired(null);
    setUserPlan(null);
    setUserDesired(null);
    setUserPassword("");
    setUserPasswordConfirm("");
    setReplacementPassword("");
    setReplacementPasswordConfirm("");
    setPasswordDesired(null);
    setPasswordPlan(null);
    setEchoLinkPlan(null);
    setEchoPassword("");
    setEchoPasswordConfirm("");
    setEchoLifecyclePlan(null);
    setEchoStatusDesired(null);
    setEchoUnlinkDesired(null);
    setEchoPasswordDesired(null);
    setEchoReplacementPassword("");
    setEchoReplacementConfirm("");
    fetchEchoAccounts()
      .then((directory) => {
        if (alive) setEchoAccounts(directory);
      })
      .catch(() => {
        if (alive) setEchoAccounts(null);
      });
    fetchOmvStatus()
      .then(async (nextStatus) => {
        if (!alive) return;
        setStatus(nextStatus);
        if (!nextStatus.available) {
          setOverview(null);
          setFilesystems([]);
          return;
        }
        const [nextOverview, nextFilesystems] = await Promise.all([
          fetchOmvSharingOverview(),
          fetchOmvFilesystems(),
        ]);
        if (alive) {
          setOverview(nextOverview);
          setFilesystems(nextFilesystems);
          const firstQuotaFilesystem = nextFilesystems.find(
            (entry) => entry.uuid && entry.supportsQuota && !entry.readOnly,
          );
          setQuotaFilesystemUuid(firstQuotaFilesystem?.uuid ?? "");
          setQuotaSubjectType("user");
          setQuotaSubjectName(nextOverview.users[0]?.name ?? "");
          setFolderMountRef(
            (current) =>
              nextOverview.sharedFolderTargets.find(
                (entry) => entry.mountPointRef === current,
              )?.mountPointRef ??
              nextOverview.sharedFolderTargets[0]?.mountPointRef ??
              "",
          );
        }
      })
      .catch((reason) => {
        if (alive) {
          setOverview(null);
          setFilesystems([]);
          setError(
            reason instanceof Error ? reason.message : "无法读取共享与用户状态",
          );
        }
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [reloadKey]);

  const readPrivileges = async (
    uuid: string,
  ): Promise<OmvSharePrivilege[] | null> => {
    setPrivilegeLoading(uuid);
    setError(null);
    try {
      const entries = await fetchOmvSharePrivileges(uuid);
      setPrivileges((current) => ({ ...current, [uuid]: entries }));
      return entries;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取共享权限");
      return null;
    } finally {
      setPrivilegeLoading(null);
    }
  };

  const beginPrivilegeControl = async (folder: OmvSharedFolder) => {
    const entries =
      privileges[folder.uuid] ?? (await readPrivileges(folder.uuid));
    const first = entries?.[0];
    if (
      !status?.capabilities?.includes("shared-folder.privilege.simple.v1") ||
      !first
    ) {
      return;
    }
    setEditingPrivilegeFolder(folder);
    setPrivilegePlan(null);
    setPrivilegeDesired({
      schema: "echo.omv.share-privilege-desired.v1",
      sharedFolderRef: folder.uuid,
      principalType: first.type,
      principalName: first.name,
      permission: first.permission,
    });
  };

  const selectPrivilegePrincipal = (identity: string) => {
    if (!editingPrivilegeFolder) return;
    const entry = privileges[editingPrivilegeFolder.uuid]?.find(
      (candidate) => `${candidate.type}:${candidate.id}` === identity,
    );
    if (!entry) return;
    setPrivilegeDesired({
      schema: "echo.omv.share-privilege-desired.v1",
      sharedFolderRef: editingPrivilegeFolder.uuid,
      principalType: entry.type,
      principalName: entry.name,
      permission: entry.permission,
    });
    setPrivilegePlan(null);
  };

  const previewPrivilegeChange = async () => {
    if (!privilegeDesired) return;
    setPrivilegePlanning(true);
    setError(null);
    try {
      setPrivilegePlan(await planOmvSharePrivilege(privilegeDesired));
    } catch (reason) {
      setPrivilegePlan(null);
      setError(
        reason instanceof Error ? reason.message : "无法生成共享权限变更预览",
      );
    } finally {
      setPrivilegePlanning(false);
    }
  };

  const confirmPrivilegeChange = async (password: string) => {
    if (!privilegeDesired || !privilegePlan) return;
    const approval = await requestHighRiskApproval(
      "omv.share-privilege.apply",
      privilegePlan.planId,
      password,
    );
    await applyOmvSharePrivilege(
      privilegeDesired,
      privilegePlan.planId,
      approval.approvalToken,
    );
    const folderUuid = privilegeDesired.sharedFolderRef;
    setPrivilegeApprovalOpen(false);
    setEditingPrivilegeFolder(null);
    setPrivilegeDesired(null);
    setPrivilegePlan(null);
    await readPrivileges(folderUuid);
  };

  const clearFolderPreview = () => {
    setFolderDesired(null);
    setFolderPlan(null);
  };

  const previewSharedFolder = async () => {
    const name = folderName.trim();
    if (
      !folderMountRef ||
      !PORTABLE_SHARE_NAME.test(name) ||
      name.includes("..")
    ) {
      setError(
        "文件夹名称只能用 1–64 位英文、数字、点、短横线或下划线，不能以点开头/结尾或包含连续两点",
      );
      setFolderPlan(null);
      return;
    }
    const nextDesired: OmvSharedFolderDesiredState = {
      schema: "echo.omv.shared-folder-desired.v1",
      mountPointRef: folderMountRef,
      name,
      comment: folderComment,
    };
    setFolderPlanning(true);
    setFolderDesired(nextDesired);
    setError(null);
    try {
      setFolderPlan(await planOmvSharedFolder(nextDesired));
    } catch (reason) {
      setFolderPlan(null);
      setError(
        reason instanceof Error ? reason.message : "无法生成共享文件夹创建预览",
      );
    } finally {
      setFolderPlanning(false);
    }
  };

  const confirmSharedFolder = async (password: string) => {
    if (!folderDesired || !folderPlan) return;
    const approval = await requestHighRiskApproval(
      "omv.shared-folder.create",
      folderPlan.planId,
      password,
    );
    await applyOmvSharedFolder(
      folderDesired,
      folderPlan.planId,
      approval.approvalToken,
    );
    setFolderApprovalOpen(false);
    setFolderCreateOpen(false);
    setFolderName("");
    setFolderComment("");
    clearFolderPreview();
    setReloadKey((value) => value + 1);
  };

  const beginSmbControl = (folder: OmvSharedFolder) => {
    const existing = overview?.smb.shares.find(
      (share) => share.sharedFolderRef === folder.uuid,
    );
    setEditingFolder(folder);
    setPlan(null);
    setDesired({
      schema: "echo.omv.smb-share-desired.v1",
      sharedFolderRef: folder.uuid,
      enabled: existing?.enabled ?? true,
      readOnly: existing?.readOnly ?? true,
      browseable: existing?.browseable ?? true,
      recycleBin: existing?.recycleBin ?? true,
      comment: existing?.comment ?? folder.comment ?? "",
    });
  };

  const previewSmbChange = async () => {
    if (!desired) return;
    setPlanning(true);
    setError(null);
    try {
      setPlan(await planOmvSmbShare(desired));
    } catch (reason) {
      setPlan(null);
      setError(
        reason instanceof Error ? reason.message : "无法生成 SMB 变更预览",
      );
    } finally {
      setPlanning(false);
    }
  };

  const confirmSmbChange = async (password: string) => {
    if (!desired || !plan) return;
    const approval = await requestHighRiskApproval(
      "omv.smb.apply",
      plan.planId,
      password,
    );
    await applyOmvSmbShare(desired, plan.planId, approval.approvalToken);
    setApprovalOpen(false);
    setEditingFolder(null);
    setDesired(null);
    setPlan(null);
    setReloadKey((value) => value + 1);
  };

  const updateDesired = (change: Partial<OmvSmbDesiredState>) => {
    setDesired((current) => (current ? { ...current, ...change } : current));
    setPlan(null);
  };

  const beginNfsControl = (folder: OmvSharedFolder) => {
    const existing = overview?.nfs.shares.find(
      (share) => share.sharedFolderRef === folder.uuid,
    );
    setEditingNfsFolder(folder);
    setNfsPlan(null);
    setNfsDesired({
      schema: "echo.omv.nfs-share-desired.v1",
      sharedFolderRef: folder.uuid,
      clientCidr: existing?.client ?? "192.168.1.0/24",
      readOnly: existing?.options === "ro" || !existing,
      comment: existing?.comment ?? folder.comment ?? "",
    });
  };

  const updateNfsDesired = (change: Partial<OmvNfsDesiredState>) => {
    setNfsDesired((current) => (current ? { ...current, ...change } : current));
    setNfsPlan(null);
  };

  const previewNfsChange = async () => {
    if (!nfsDesired) return;
    setNfsPlanning(true);
    setError(null);
    try {
      setNfsPlan(await planOmvNfsShare(nfsDesired));
    } catch (reason) {
      setNfsPlan(null);
      setError(
        reason instanceof Error ? reason.message : "无法生成 NFS 变更预览",
      );
    } finally {
      setNfsPlanning(false);
    }
  };

  const confirmNfsChange = async (password: string) => {
    if (!nfsDesired || !nfsPlan) return;
    const approval = await requestHighRiskApproval(
      "omv.nfs.apply",
      nfsPlan.planId,
      password,
    );
    await applyOmvNfsShare(nfsDesired, nfsPlan.planId, approval.approvalToken);
    setNfsApprovalOpen(false);
    setEditingNfsFolder(null);
    setNfsDesired(null);
    setNfsPlan(null);
    setReloadKey((value) => value + 1);
  };

  const clearQuotaPreview = () => {
    setQuotaDesired(null);
    setQuotaPlan(null);
  };

  const previewQuotaChange = async () => {
    const gib = Number(quotaLimitGiB);
    if (
      !Number.isSafeInteger(gib) ||
      gib < 0 ||
      gib > MAX_SAFE_QUOTA_GIB ||
      !quotaFilesystemUuid ||
      !quotaSubjectName
    ) {
      setError(`硬限制必须是 0–${MAX_SAFE_QUOTA_GIB} 之间的整数 GiB`);
      setQuotaPlan(null);
      return;
    }
    const nextDesired: OmvQuotaDesiredState = {
      schema: "echo.omv.filesystem-quota-desired.v1",
      filesystemUuid: quotaFilesystemUuid,
      subjectType: quotaSubjectType,
      subjectName: quotaSubjectName,
      hardLimitBytes: gib * GIB_BYTES,
    };
    setQuotaPlanning(true);
    setError(null);
    setQuotaDesired(nextDesired);
    try {
      setQuotaPlan(await planOmvFilesystemQuota(nextDesired));
    } catch (reason) {
      setQuotaPlan(null);
      setError(
        reason instanceof Error ? reason.message : "无法生成文件系统配额预览",
      );
    } finally {
      setQuotaPlanning(false);
    }
  };

  const confirmQuotaChange = async (password: string) => {
    if (!quotaDesired || !quotaPlan) return;
    const approval = await requestHighRiskApproval(
      "omv.quota.apply",
      quotaPlan.planId,
      password,
    );
    await applyOmvFilesystemQuota(
      quotaDesired,
      quotaPlan.planId,
      approval.approvalToken,
    );
    setQuotaApprovalOpen(false);
    clearQuotaPreview();
    setReloadKey((value) => value + 1);
  };

  const clearGroupPreview = () => {
    setGroupDesired(null);
    setGroupPlan(null);
  };

  const previewGroup = async () => {
    const name = groupName.trim();
    if (!validFamilyAccountName(name)) {
      setError(
        "用户组名称必须以小写字母开头，只能使用小写字母、数字、短横线或下划线，且不能使用系统保留名称",
      );
      clearGroupPreview();
      return;
    }
    const nextDesired: OmvGroupDesiredState = {
      schema: "echo.omv.group-desired.v1",
      name,
      comment: groupComment.trim(),
    };
    setGroupPlanning(true);
    setGroupDesired(nextDesired);
    setError(null);
    try {
      setGroupPlan(await planOmvGroup(nextDesired));
    } catch (reason) {
      setGroupPlan(null);
      setError(
        reason instanceof Error ? reason.message : "无法生成用户组创建预览",
      );
    } finally {
      setGroupPlanning(false);
    }
  };

  const confirmGroup = async (password: string) => {
    if (!groupDesired || !groupPlan) return;
    const approval = await requestHighRiskApproval(
      "omv.group.create",
      groupPlan.planId,
      password,
    );
    await applyOmvGroup(groupDesired, groupPlan.planId, approval.approvalToken);
    setGroupApprovalOpen(false);
    setAccountMode(null);
    setGroupName("");
    setGroupComment("");
    clearGroupPreview();
    setReloadKey((value) => value + 1);
  };

  const clearUserPreviewSecret = () => {
    setUserPassword("");
    setUserPasswordConfirm("");
    setUserDesired(null);
    setUserPlan(null);
  };

  const closeUserEditor = () => {
    setUserApprovalOpen(false);
    setAccountMode(null);
    setUserName("");
    setUserDisplayName("");
    setUserGroups([]);
    clearUserPreviewSecret();
  };

  const previewUser = async () => {
    const name = userName.trim();
    const displayName = userDisplayName.trim();
    const categoryCount = [
      /[a-z]/.test(userPassword),
      /[A-Z]/.test(userPassword),
      /[0-9]/.test(userPassword),
      /[^A-Za-z0-9]/.test(userPassword),
    ].filter(Boolean).length;
    if (!validFamilyAccountName(name)) {
      setError(
        "成员账号必须以小写字母开头，只能使用小写字母、数字、短横线或下划线，且不能使用系统保留名称",
      );
      setUserPlan(null);
      return;
    }
    if (!displayName) {
      setError("请填写家庭成员显示名称");
      setUserPlan(null);
      return;
    }
    if (
      userPassword.length < 12 ||
      userPassword.length > 128 ||
      userPassword.toLocaleLowerCase() === name.toLocaleLowerCase() ||
      /[\u0000-\u001f]/.test(userPassword) ||
      (userPassword.length < 20 && categoryCount < 3)
    ) {
      setError(
        "成员密码需为 12–128 位并包含至少三类字符，或使用不少于 20 位的长口令，且不能与账号相同",
      );
      setUserPlan(null);
      return;
    }
    if (userPassword !== userPasswordConfirm) {
      setError("两次输入的成员密码不一致");
      setUserPlan(null);
      return;
    }
    const nextDesired: OmvUserDesiredState = {
      schema: "echo.omv.user-desired.v1",
      name,
      displayName,
      password: userPassword,
      groups: [...userGroups].sort(),
    };
    setUserPlanning(true);
    setUserDesired(nextDesired);
    setError(null);
    try {
      const nextPlan = await planOmvUser(nextDesired);
      setUserPlan(nextPlan);
    } catch (reason) {
      setUserPlan(null);
      setError(
        reason instanceof Error ? reason.message : "无法生成家庭成员创建预览",
      );
    } finally {
      setUserPlanning(false);
    }
  };

  const confirmUser = async (password: string) => {
    if (!userDesired || !userPlan) return;
    try {
      const approval = await requestHighRiskApproval(
        "omv.user.create",
        userPlan.planId,
        password,
      );
      await applyOmvUser(userDesired, userPlan.planId, approval.approvalToken);
    } catch (reason) {
      setUserApprovalOpen(false);
      clearUserPreviewSecret();
      setError(
        "家庭成员创建未完成；成员密码已从界面清除，请重新输入并预览后再试",
      );
      throw reason;
    }
    closeUserEditor();
    setReloadKey((value) => value + 1);
  };

  const clearPasswordResetSecret = () => {
    setReplacementPassword("");
    setReplacementPasswordConfirm("");
    setPasswordDesired(null);
    setPasswordPlan(null);
  };

  const closePasswordReset = () => {
    setPasswordApprovalOpen(false);
    setAccountMode(null);
    setPasswordUserName("");
    clearPasswordResetSecret();
  };

  const beginPasswordReset = (name: string) => {
    clearGroupPreview();
    clearUserPreviewSecret();
    clearPasswordResetSecret();
    clearEchoLinkSecret();
    setPasswordUserName(name);
    setAccountMode("password");
  };

  const previewPasswordReset = async () => {
    const categoryCount = [
      /[a-z]/.test(replacementPassword),
      /[A-Z]/.test(replacementPassword),
      /[0-9]/.test(replacementPassword),
      /[^A-Za-z0-9]/.test(replacementPassword),
    ].filter(Boolean).length;
    if (
      !validFamilyAccountName(passwordUserName) ||
      replacementPassword.length < 12 ||
      replacementPassword.length > 128 ||
      replacementPassword.toLocaleLowerCase() ===
        passwordUserName.toLocaleLowerCase() ||
      /[\u0000-\u001f]/.test(replacementPassword) ||
      (replacementPassword.length < 20 && categoryCount < 3)
    ) {
      setError(
        "新密码需为 12–128 位并包含至少三类字符，或使用不少于 20 位的长口令，且不能与账号相同",
      );
      setPasswordPlan(null);
      return;
    }
    if (replacementPassword !== replacementPasswordConfirm) {
      setError("两次输入的新密码不一致");
      setPasswordPlan(null);
      return;
    }
    const nextDesired: OmvUserPasswordDesiredState = {
      schema: "echo.omv.user-password-desired.v1",
      name: passwordUserName,
      password: replacementPassword,
    };
    setPasswordPlanning(true);
    setPasswordDesired(nextDesired);
    setError(null);
    try {
      setPasswordPlan(await planOmvUserPassword(nextDesired));
    } catch (reason) {
      setPasswordPlan(null);
      setError(
        reason instanceof Error ? reason.message : "无法生成成员密码重置预览",
      );
    } finally {
      setPasswordPlanning(false);
    }
  };

  const confirmPasswordReset = async (password: string) => {
    if (!passwordDesired || !passwordPlan) return;
    try {
      const approval = await requestHighRiskApproval(
        "omv.user.password.reset",
        passwordPlan.planId,
        password,
      );
      await applyOmvUserPassword(
        passwordDesired,
        passwordPlan.planId,
        approval.approvalToken,
      );
    } catch (reason) {
      setPasswordApprovalOpen(false);
      clearPasswordResetSecret();
      setError(
        "成员密码重置未确认完成；新密码已从界面清除。凭据状态可能已改变，请重新输入、预览并重试登录验证。",
      );
      throw reason;
    }
    closePasswordReset();
    setReloadKey((value) => value + 1);
  };

  const clearEchoLinkSecret = () => {
    setEchoPassword("");
    setEchoPasswordConfirm("");
    setEchoLinkPlan(null);
  };

  const beginEchoLink = (username: string, displayName: string) => {
    clearGroupPreview();
    clearUserPreviewSecret();
    clearPasswordResetSecret();
    clearEchoLinkSecret();
    setEchoMemberName(username);
    setEchoDisplayName(displayName.trim().slice(0, 64) || username);
    setAccountMode("echo");
  };

  const closeEchoLink = () => {
    setEchoLinkApprovalOpen(false);
    setAccountMode(null);
    setEchoMemberName("");
    setEchoDisplayName("");
    clearEchoLinkSecret();
  };

  const previewEchoLink = async () => {
    const categoryCount = [
      /[a-z]/.test(echoPassword),
      /[A-Z]/.test(echoPassword),
      /[0-9]/.test(echoPassword),
      /[^A-Za-z0-9]/.test(echoPassword),
    ].filter(Boolean).length;
    if (
      echoPassword.length < 12 ||
      echoPassword.length > 72 ||
      echoPassword.toLocaleLowerCase() === echoMemberName.toLocaleLowerCase() ||
      /[\u0000-\u001f]/.test(echoPassword) ||
      (echoPassword.length < 20 && categoryCount < 3)
    ) {
      setError(
        "Echo 密码需为 12–72 位并包含至少三类字符，或使用不少于 20 位的长口令，且不能与账号相同",
      );
      setEchoLinkPlan(null);
      return;
    }
    if (echoPassword !== echoPasswordConfirm) {
      setError("两次输入的 Echo 登录密码不一致");
      setEchoLinkPlan(null);
      return;
    }
    const desired: EchoAccountLinkDesired = {
      omvUsername: echoMemberName,
      displayName: echoDisplayName.trim(),
      password: echoPassword,
    };
    setEchoLinkPlanning(true);
    setError(null);
    try {
      setEchoLinkPlan(await planEchoAccountLink(desired));
    } catch (reason) {
      setEchoLinkPlan(null);
      setError(
        reason instanceof Error ? reason.message : "无法预览 Echo 登录开通",
      );
    } finally {
      setEchoLinkPlanning(false);
    }
  };

  const confirmEchoLink = async (administratorPassword: string) => {
    if (!echoLinkPlan) return;
    const desired: EchoAccountLinkDesired = {
      omvUsername: echoMemberName,
      displayName: echoDisplayName.trim(),
      password: echoPassword,
    };
    try {
      const approval = await requestHighRiskApproval(
        "account.member.link",
        echoLinkPlan.planId,
        administratorPassword,
      );
      await applyEchoAccountLink(
        desired,
        echoLinkPlan.planId,
        approval.approvalToken,
      );
    } catch (reason) {
      setEchoLinkApprovalOpen(false);
      clearEchoLinkSecret();
      setError(reason instanceof Error ? reason.message : "Echo 登录开通失败");
      throw reason;
    }
    closeEchoLink();
    setReloadKey((value) => value + 1);
  };

  const beginEchoPasswordReset = (username: string) => {
    clearGroupPreview();
    clearUserPreviewSecret();
    clearPasswordResetSecret();
    clearEchoLinkSecret();
    setEchoLifecycleMember(username);
    setEchoReplacementPassword("");
    setEchoReplacementConfirm("");
    setEchoPasswordDesired(null);
    setEchoLifecyclePlan(null);
    setAccountMode("echoPassword");
  };

  const previewEchoPasswordReset = async () => {
    if (echoReplacementPassword !== echoReplacementConfirm) {
      setError("两次输入的 Echo 登录密码不一致");
      return;
    }
    const desired: EchoAccountPasswordDesired = {
      username: echoLifecycleMember,
      newPassword: echoReplacementPassword,
    };
    setEchoLifecyclePlanning(true);
    setError(null);
    try {
      setEchoPasswordDesired(desired);
      setEchoLifecyclePlan(await planEchoAccountPassword(desired));
    } catch (reason) {
      setEchoPasswordDesired(null);
      setEchoLifecyclePlan(null);
      setError(
        reason instanceof Error ? reason.message : "无法预览 Echo 密码重置",
      );
    } finally {
      setEchoLifecyclePlanning(false);
    }
  };

  const previewEchoStatus = async (username: string, active: boolean) => {
    const desired = { username, active };
    setEchoLifecyclePlanning(true);
    setError(null);
    try {
      setEchoLifecycleMember(username);
      setEchoStatusDesired(desired);
      setEchoLifecyclePlan(await planEchoAccountStatus(desired));
      setEchoStatusApprovalOpen(true);
    } catch (reason) {
      setEchoStatusDesired(null);
      setEchoLifecyclePlan(null);
      setError(
        reason instanceof Error ? reason.message : "无法预览成员状态变更",
      );
    } finally {
      setEchoLifecyclePlanning(false);
    }
  };

  const confirmEchoStatus = async (administratorPassword: string) => {
    if (!echoStatusDesired || !echoLifecyclePlan) return;
    const approval = await requestHighRiskApproval(
      "account.member.status.set",
      echoLifecyclePlan.planId,
      administratorPassword,
    );
    await applyEchoAccountStatus(
      echoStatusDesired,
      echoLifecyclePlan.planId,
      approval.approvalToken,
    );
    setEchoStatusApprovalOpen(false);
    setEchoStatusDesired(null);
    setEchoLifecyclePlan(null);
    setReloadKey((value) => value + 1);
  };

  const previewEchoUnlink = async (username: string) => {
    const desired = { username };
    setEchoLifecyclePlanning(true);
    setError(null);
    try {
      setEchoLifecycleMember(username);
      setEchoUnlinkDesired(desired);
      setEchoLifecyclePlan(await planEchoAccountUnlink(desired));
      setEchoUnlinkApprovalOpen(true);
    } catch (reason) {
      setEchoUnlinkDesired(null);
      setEchoLifecyclePlan(null);
      setError(
        reason instanceof Error ? reason.message : "无法预览 Echo 登录移除",
      );
    } finally {
      setEchoLifecyclePlanning(false);
    }
  };

  const confirmEchoUnlink = async (administratorPassword: string) => {
    if (!echoUnlinkDesired || !echoLifecyclePlan) return;
    const approval = await requestHighRiskApproval(
      "account.member.unlink",
      echoLifecyclePlan.planId,
      administratorPassword,
    );
    await applyEchoAccountUnlink(
      echoUnlinkDesired,
      echoLifecyclePlan.planId,
      approval.approvalToken,
    );
    setEchoUnlinkApprovalOpen(false);
    setEchoUnlinkDesired(null);
    setEchoLifecyclePlan(null);
    setReloadKey((value) => value + 1);
  };

  const confirmEchoPasswordReset = async (administratorPassword: string) => {
    if (!echoPasswordDesired || !echoLifecyclePlan) return;
    try {
      const approval = await requestHighRiskApproval(
        "account.member.password.reset",
        echoLifecyclePlan.planId,
        administratorPassword,
      );
      await applyEchoAccountPassword(
        echoPasswordDesired,
        echoLifecyclePlan.planId,
        approval.approvalToken,
      );
    } catch (reason) {
      setEchoPasswordApprovalOpen(false);
      setEchoReplacementPassword("");
      setEchoReplacementConfirm("");
      setError(reason instanceof Error ? reason.message : "Echo 密码重置失败");
      throw reason;
    }
    setEchoPasswordApprovalOpen(false);
    setEchoReplacementPassword("");
    setEchoReplacementConfirm("");
    setEchoPasswordDesired(null);
    setEchoLifecyclePlan(null);
    setAccountMode(null);
    setReloadKey((value) => value + 1);
  };

  const eligibleQuotaFilesystems = filesystems.filter(
    (entry) => entry.uuid && entry.supportsQuota && !entry.readOnly,
  );
  const quotaSubjects =
    quotaSubjectType === "user"
      ? (overview?.users.map((entry) => entry.name) ?? [])
      : (overview?.groups.map((entry) => entry.name) ?? []);
  const selectableUserGroups = (overview?.groups ?? [])
    .map((entry) => entry.name)
    .filter(validFamilyAccountName)
    .sort();

  return (
    <>
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-[24px] font-semibold tracking-tight">
            共享与用户
          </h1>
          <p className="mt-1 text-[13px] text-slate-500">
            查看 SMB / NFS、NAS 账户和共享权限
          </p>
        </div>
        <button
          type="button"
          onClick={() => setReloadKey((value) => value + 1)}
          disabled={loading}
          className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 text-xs font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:opacity-50"
        >
          <RefreshCwIcon
            className={`size-3.5 ${loading ? "animate-spin" : ""}`}
          />
          刷新
        </button>
      </header>

      <section className="mt-6 flex items-center gap-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-blue-50 text-blue-600">
          {loading ? (
            <Loader2Icon className="size-5 animate-spin" />
          ) : (
            <FoldersIcon className="size-5" />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-[15px] font-semibold">
            {loading
              ? "正在读取 OMV 配置…"
              : status?.available
                ? "OMV 是共享与账户的管理底座"
                : "OMV 共享管理尚不可用"}
          </h2>
          <p className="mt-0.5 text-xs leading-5 text-slate-500">
            Echo
            展示脱敏概览，可在现有可写卷上安全新建基础共享文件夹，并为其预览和应用简单私有
            SMB / NFS
            规则、普通家庭账户/组、受限成员密码重置及已有用户/组访问权限；其他账户修改/删除、文件系统
            ACL 和复杂协议配置仍在 OMV 完成。
          </p>
        </div>
        {status?.adminUrl && (
          <a
            href={status.adminUrl}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg bg-blue-600 px-3 text-xs font-medium text-white transition hover:bg-blue-700"
          >
            在 OMV 中管理
            <ExternalLinkIcon className="size-3.5" />
          </a>
        )}
      </section>

      {error && (
        <p
          role="alert"
          className="mt-3 rounded-xl bg-red-50 px-4 py-3 text-xs text-red-700"
        >
          {error}
        </p>
      )}

      {!loading && status?.available && !status.adminUrl && (
        <p className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800">
          已能查看配置；部署时设置 ECHO_OMV_ADMIN_URL 后，这里会出现安全的 OMV
          管理入口。
        </p>
      )}

      {overview && (
        <>
          <div className="mt-4 grid grid-cols-2 gap-3">
            {[
              {
                name: "SMB",
                enabled: overview.smb.enabled,
                count: overview.smb.shares.length,
              },
              {
                name: "NFS",
                enabled: overview.nfs.enabled,
                count: overview.nfs.shares.length,
              },
            ].map((service) => (
              <section
                key={service.name}
                className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
              >
                <div className="flex items-center justify-between">
                  <span className="inline-flex items-center gap-2 text-sm font-semibold">
                    <ServerIcon className="size-4 text-blue-500" />
                    {service.name}
                  </span>
                  <span
                    className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                      service.enabled
                        ? "bg-emerald-50 text-emerald-700"
                        : "bg-slate-100 text-slate-500"
                    }`}
                  >
                    {service.enabled ? "已启用" : "未启用"}
                  </span>
                </div>
                <p className="mt-3 text-2xl font-semibold text-slate-800">
                  {service.count}
                  <span className="ml-1 text-xs font-normal text-slate-400">
                    个共享规则
                  </span>
                </p>
              </section>
            ))}
          </div>

          <section className="mt-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-3">
              <h2 className="text-[15px] font-semibold">共享协议规则</h2>
              <p className="mt-0.5 text-[11px] text-slate-400">
                只显示允许的连接范围和访问方式，不返回密码或额外配置字段
              </p>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                <strong className="inline-flex items-center gap-1.5 text-xs text-slate-700">
                  <ServerIcon className="size-3.5 text-blue-500" />
                  SMB
                </strong>
                <div className="mt-2 space-y-1.5">
                  {overview.smb.shares.map((share) => (
                    <div
                      key={share.uuid}
                      className="rounded-lg bg-white px-2.5 py-2 text-[10px] text-slate-500 ring-1 ring-slate-200"
                    >
                      <span className="block truncate font-medium text-slate-700">
                        {share.sharedFolderName || "未命名共享"}
                      </span>
                      <span>
                        {share.enabled ? "已启用" : "已停用"} ·{" "}
                        {share.readOnly ? "只读" : "读写"} · 访客{" "}
                        {share.guest === "no" ? "关闭" : share.guest || "继承"}
                      </span>
                    </div>
                  ))}
                  {overview.smb.shares.length === 0 && (
                    <span className="text-[10px] text-slate-400">
                      没有 SMB 共享规则
                    </span>
                  )}
                </div>
              </div>
              <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                <strong className="inline-flex items-center gap-1.5 text-xs text-slate-700">
                  <ServerIcon className="size-3.5 text-blue-500" />
                  NFS
                </strong>
                <div className="mt-2 space-y-1.5">
                  {overview.nfs.shares.map((share) => (
                    <div
                      key={share.uuid}
                      className="rounded-lg bg-white px-2.5 py-2 text-[10px] text-slate-500 ring-1 ring-slate-200"
                    >
                      <span className="block truncate font-medium text-slate-700">
                        {share.sharedFolderName || "未命名共享"}
                      </span>
                      <span className="block truncate">
                        客户端 {share.client || "未指定"} ·{" "}
                        {share.options || "默认选项"}
                      </span>
                    </div>
                  ))}
                  {overview.nfs.shares.length === 0 && (
                    <span className="text-[10px] text-slate-400">
                      没有 NFS 共享规则
                    </span>
                  )}
                </div>
              </div>
            </div>
          </section>

          {editingFolder && desired && (
            <section className="mt-4 rounded-2xl border border-blue-200 bg-blue-50/60 p-5 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-[15px] font-semibold">
                    SMB 期望状态 · {editingFolder.name}
                  </h2>
                  <p className="mt-0.5 text-[11px] leading-5 text-slate-500">
                    只管理简单私有规则；访客、ACL、用户和高级 Samba
                    选项不会被开放。
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setEditingFolder(null);
                    setDesired(null);
                    setPlan(null);
                  }}
                  className="shrink-0 whitespace-nowrap text-xs text-slate-500 hover:text-slate-800"
                >
                  关闭
                </button>
              </div>

              <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {smbBooleanFields.map(({ field, label }) => (
                  <label
                    key={field}
                    className="flex items-center gap-2 rounded-xl border border-blue-100 bg-white px-3 py-2.5 text-xs text-slate-700"
                  >
                    <input
                      type="checkbox"
                      checked={Boolean(
                        desired[field as keyof OmvSmbDesiredState],
                      )}
                      onChange={(event) =>
                        updateDesired({ [field]: event.currentTarget.checked })
                      }
                      className="size-3.5 rounded border-slate-300"
                    />
                    {label}
                  </label>
                ))}
              </div>
              <label className="mt-3 block text-xs font-medium text-slate-600">
                备注
                <input
                  value={desired.comment}
                  maxLength={512}
                  onChange={(event) =>
                    updateDesired({ comment: event.currentTarget.value })
                  }
                  className="mt-1.5 h-9 w-full rounded-xl border border-blue-100 bg-white px-3 text-xs outline-none focus:border-blue-500"
                  placeholder="这个共享用于什么"
                />
              </label>
              <div className="mt-4 flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => void previewSmbChange()}
                  disabled={planning}
                  className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-blue-600 px-4 text-xs font-medium text-white transition hover:bg-blue-700 disabled:opacity-50"
                >
                  {planning && (
                    <Loader2Icon className="size-3.5 animate-spin" />
                  )}
                  {planning ? "正在预览…" : "预览变更"}
                </button>
                <span className="text-[11px] text-slate-500">
                  预览不会修改 OMV
                </span>
              </div>

              {plan && (
                <div className="mt-4 rounded-xl border border-blue-100 bg-white p-3">
                  <div className="flex items-center justify-between gap-3">
                    <strong className="text-xs text-slate-800">
                      {plan.operation === "create"
                        ? "将创建 SMB 规则"
                        : plan.operation === "update"
                          ? "将更新 SMB 规则"
                          : "当前已经符合期望状态"}
                    </strong>
                    {plan.requiresApproval && (
                      <button
                        type="button"
                        onClick={() => setApprovalOpen(true)}
                        className="h-8 rounded-lg bg-amber-500 px-3 text-[11px] font-medium text-white hover:bg-amber-600"
                      >
                        管理员确认并应用
                      </button>
                    )}
                  </div>
                  {plan.changes.length > 0 && (
                    <div className="mt-2 space-y-1.5">
                      {plan.changes.map((change) => (
                        <div
                          key={change.field}
                          className="grid grid-cols-[90px_1fr_auto_1fr] items-center gap-2 rounded-lg bg-slate-50 px-2.5 py-2 text-[10px] text-slate-500"
                        >
                          <span className="font-medium text-slate-700">
                            {smbFieldLabel[change.field]}
                          </span>
                          <span className="truncate">
                            {planValue(change.before)}
                          </span>
                          <span>→</span>
                          <span className="truncate font-medium text-blue-700">
                            {planValue(change.after)}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </section>
          )}

          {editingNfsFolder && nfsDesired && (
            <section className="mt-4 rounded-2xl border border-emerald-200 bg-emerald-50/60 p-5 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-[15px] font-semibold">
                    NFS 私网规则 · {editingNfsFolder.name}
                  </h2>
                  <p className="mt-0.5 text-[11px] leading-5 text-slate-500">
                    只允许一个 RFC1918 或 IPv6 ULA 网段，强制
                    root_squash、同步写入和
                    subtree_check；通配符、公网和高级参数不会被开放。
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setEditingNfsFolder(null);
                    setNfsDesired(null);
                    setNfsPlan(null);
                  }}
                  className="text-xs text-slate-500 hover:text-slate-800"
                >
                  关闭
                </button>
              </div>
              <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-[1fr_auto]">
                <label className="text-xs font-medium text-slate-600">
                  允许挂载的私网 CIDR
                  <input
                    value={nfsDesired.clientCidr}
                    maxLength={64}
                    onChange={(event) =>
                      updateNfsDesired({
                        clientCidr: event.currentTarget.value,
                      })
                    }
                    className="mt-1.5 h-9 w-full rounded-xl border border-emerald-100 bg-white px-3 text-xs outline-none focus:border-emerald-500"
                    placeholder="192.168.1.0/24"
                  />
                </label>
                <label className="flex items-end gap-2 pb-2 text-xs text-slate-700">
                  <input
                    type="checkbox"
                    checked={nfsDesired.readOnly}
                    onChange={(event) =>
                      updateNfsDesired({
                        readOnly: event.currentTarget.checked,
                      })
                    }
                    className="size-3.5 rounded border-slate-300"
                  />
                  只读访问
                </label>
              </div>
              <label className="mt-3 block text-xs font-medium text-slate-600">
                备注
                <input
                  value={nfsDesired.comment}
                  maxLength={512}
                  onChange={(event) =>
                    updateNfsDesired({ comment: event.currentTarget.value })
                  }
                  className="mt-1.5 h-9 w-full rounded-xl border border-emerald-100 bg-white px-3 text-xs outline-none focus:border-emerald-500"
                  placeholder="这个 NFS 规则用于什么"
                />
              </label>
              <div className="mt-4 flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => void previewNfsChange()}
                  disabled={nfsPlanning || !nfsDesired.clientCidr.trim()}
                  className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-emerald-600 px-4 text-xs font-medium text-white transition hover:bg-emerald-700 disabled:opacity-50"
                >
                  {nfsPlanning && (
                    <Loader2Icon className="size-3.5 animate-spin" />
                  )}
                  {nfsPlanning ? "正在预览…" : "预览 NFS 变更"}
                </button>
                <span className="text-[11px] text-slate-500">
                  预览不会修改 OMV
                </span>
              </div>
              {nfsPlan && (
                <div className="mt-4 rounded-xl border border-emerald-100 bg-white p-3">
                  <div className="flex items-center justify-between gap-3">
                    <strong className="text-xs text-slate-800">
                      {nfsPlan.operation === "create"
                        ? "将创建 NFS 私网规则"
                        : nfsPlan.operation === "update"
                          ? "将更新 NFS 私网规则"
                          : "当前已经符合期望状态"}
                    </strong>
                    {nfsPlan.requiresApproval && (
                      <button
                        type="button"
                        onClick={() => setNfsApprovalOpen(true)}
                        className="h-8 rounded-lg bg-amber-500 px-3 text-[11px] font-medium text-white hover:bg-amber-600"
                      >
                        管理员确认并应用
                      </button>
                    )}
                  </div>
                  <div className="mt-2 space-y-1.5">
                    {nfsPlan.changes.map((change) => (
                      <div
                        key={change.field}
                        className="grid grid-cols-[90px_1fr_auto_1fr] items-center gap-2 rounded-lg bg-slate-50 px-2.5 py-2 text-[10px] text-slate-500"
                      >
                        <span className="font-medium text-slate-700">
                          {nfsFieldLabel[change.field]}
                        </span>
                        <span className="truncate">
                          {planValue(change.before)}
                        </span>
                        <span>→</span>
                        <span className="truncate font-medium text-emerald-700">
                          {planValue(change.after)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </section>
          )}

          {status?.capabilities?.includes("filesystem.quota.user-group.v1") && (
            <section className="mt-4 rounded-2xl border border-violet-200 bg-violet-50/60 p-5 shadow-sm">
              <div className="flex items-start gap-3">
                <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-violet-100 text-violet-700">
                  <GaugeIcon className="size-4.5" />
                </span>
                <div className="min-w-0 flex-1">
                  <h2 className="text-[15px] font-semibold">文件系统硬配额</h2>
                  <p className="mt-0.5 text-[11px] leading-5 text-slate-500">
                    按这个卷上的文件所有者统计，限制同时作用于本机、SMB 和
                    NFS；这不是某个共享文件夹的独立空间上限。
                  </p>
                </div>
                <span className="rounded-full bg-white px-2 py-1 text-[10px] font-medium text-violet-700 ring-1 ring-violet-200">
                  OMV 原生配额
                </span>
              </div>

              {eligibleQuotaFilesystems.length === 0 ? (
                <p className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-[11px] leading-5 text-amber-800">
                  当前没有同时满足“已挂载、可写、支持配额”的文件系统。请先在 OMV
                  中启用并检查卷配额能力。
                </p>
              ) : (
                <>
                  <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-4">
                    <label className="text-[11px] font-medium text-slate-600 sm:col-span-2">
                      文件系统
                      <select
                        value={quotaFilesystemUuid}
                        onChange={(event) => {
                          setQuotaFilesystemUuid(event.currentTarget.value);
                          clearQuotaPreview();
                        }}
                        className="mt-1.5 h-9 w-full rounded-xl border border-violet-100 bg-white px-3 text-xs outline-none focus:border-violet-500"
                      >
                        {eligibleQuotaFilesystems.map((entry) => (
                          <option key={entry.uuid!} value={entry.uuid!}>
                            {entry.label || entry.devicefile} · {entry.type}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="text-[11px] font-medium text-slate-600">
                      对象类型
                      <select
                        value={quotaSubjectType}
                        onChange={(event) => {
                          const nextType = event.currentTarget.value as
                            | "user"
                            | "group";
                          const nextSubjects =
                            nextType === "user"
                              ? (overview?.users ?? [])
                              : (overview?.groups ?? []);
                          setQuotaSubjectType(nextType);
                          setQuotaSubjectName(nextSubjects[0]?.name ?? "");
                          clearQuotaPreview();
                        }}
                        className="mt-1.5 h-9 w-full rounded-xl border border-violet-100 bg-white px-3 text-xs outline-none focus:border-violet-500"
                      >
                        <option value="user">用户</option>
                        <option value="group">用户组</option>
                      </select>
                    </label>
                    <label className="text-[11px] font-medium text-slate-600">
                      {quotaSubjectType === "user" ? "用户" : "用户组"}
                      <select
                        value={quotaSubjectName}
                        onChange={(event) => {
                          setQuotaSubjectName(event.currentTarget.value);
                          clearQuotaPreview();
                        }}
                        className="mt-1.5 h-9 w-full rounded-xl border border-violet-100 bg-white px-3 text-xs outline-none focus:border-violet-500"
                      >
                        {quotaSubjects.map((name) => (
                          <option key={name} value={name}>
                            {name}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>

                  <div className="mt-3 flex flex-wrap items-end gap-3">
                    <label className="w-48 text-[11px] font-medium text-slate-600">
                      硬限制（GiB）
                      <input
                        type="number"
                        min={0}
                        max={MAX_SAFE_QUOTA_GIB}
                        step={1}
                        inputMode="numeric"
                        value={quotaLimitGiB}
                        onChange={(event) => {
                          setQuotaLimitGiB(event.currentTarget.value);
                          clearQuotaPreview();
                        }}
                        className="mt-1.5 h-9 w-full rounded-xl border border-violet-100 bg-white px-3 text-xs outline-none focus:border-violet-500"
                      />
                    </label>
                    <button
                      type="button"
                      onClick={() => void previewQuotaChange()}
                      disabled={quotaPlanning || !quotaSubjectName}
                      className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-violet-600 px-4 text-xs font-medium text-white transition hover:bg-violet-700 disabled:opacity-50"
                    >
                      {quotaPlanning && (
                        <Loader2Icon className="size-3.5 animate-spin" />
                      )}
                      {quotaPlanning ? "正在预览…" : "预览配额变更"}
                    </button>
                    <span className="pb-2 text-[10px] text-slate-500">
                      输入 0 表示取消硬限制；预览不会修改 OMV
                    </span>
                  </div>

                  {quotaPlan && (
                    <div className="mt-4 rounded-xl border border-violet-100 bg-white p-3">
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <strong className="block text-xs text-slate-800">
                            {quotaPlan.operation === "none"
                              ? "当前已经符合期望状态"
                              : [
                                  "将更新",
                                  quotaPlan.subject.type === "user"
                                    ? "用户"
                                    : "用户组",
                                  " ",
                                  quotaPlan.subject.name,
                                  " 的硬配额",
                                ].join("")}
                          </strong>
                          <span className="mt-0.5 block text-[10px] text-slate-500">
                            {quotaPlan.filesystem.label || "未命名卷"} ·
                            当前用量 {quotaPlan.subject.used}
                          </span>
                        </div>
                        {quotaPlan.requiresApproval && (
                          <button
                            type="button"
                            onClick={() => setQuotaApprovalOpen(true)}
                            className="h-8 rounded-lg bg-amber-500 px-3 text-[11px] font-medium text-white hover:bg-amber-600"
                          >
                            管理员确认并应用配额
                          </button>
                        )}
                      </div>
                      {quotaPlan.changes.map((change) => (
                        <div
                          key={change.field}
                          className="mt-2 grid grid-cols-[90px_1fr_auto_1fr] items-center gap-2 rounded-lg bg-slate-50 px-2.5 py-2 text-[10px] text-slate-500"
                        >
                          <span className="font-medium text-slate-700">
                            文件系统硬限制
                          </span>
                          <span>{quotaLimitLabel(change.before)}</span>
                          <span>→</span>
                          <span className="font-medium text-violet-700">
                            {quotaLimitLabel(change.after)}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </section>
          )}

          {editingPrivilegeFolder && privilegeDesired && (
            <section className="mt-4 rounded-2xl border border-amber-200 bg-amber-50/60 p-5 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-[15px] font-semibold">
                    用户/组访问权限 · {editingPrivilegeFolder.name}
                  </h2>
                  <p className="mt-0.5 text-[11px] leading-5 text-slate-500">
                    只管理 OMV
                    中已经存在的用户或组及其共享服务权限；不会创建账户、修改
                    POSIX ACL、递归改文件权限或删除数据。
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setEditingPrivilegeFolder(null);
                    setPrivilegeDesired(null);
                    setPrivilegePlan(null);
                  }}
                  className="text-xs text-slate-500 hover:text-slate-800"
                >
                  关闭
                </button>
              </div>

              <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label className="text-[11px] font-medium text-slate-600">
                  已有用户或组
                  <select
                    value={(() => {
                      const selected = privileges[
                        editingPrivilegeFolder.uuid
                      ]?.find(
                        (entry) =>
                          entry.type === privilegeDesired.principalType &&
                          entry.name === privilegeDesired.principalName,
                      );
                      return selected ? `${selected.type}:${selected.id}` : "";
                    })()}
                    onChange={(event) =>
                      selectPrivilegePrincipal(event.currentTarget.value)
                    }
                    className="mt-1.5 h-9 w-full rounded-xl border border-amber-100 bg-white px-3 text-xs outline-none focus:border-amber-500"
                  >
                    {(privileges[editingPrivilegeFolder.uuid] ?? []).map(
                      (entry) => (
                        <option
                          key={`${entry.type}:${entry.id}`}
                          value={`${entry.type}:${entry.id}`}
                        >
                          {entry.type === "user" ? "用户" : "用户组"} ·{" "}
                          {entry.name}
                        </option>
                      ),
                    )}
                  </select>
                </label>
                <label className="text-[11px] font-medium text-slate-600">
                  共享访问
                  <select
                    value={privilegeDesired.permission}
                    onChange={(event) => {
                      const permission = event.currentTarget
                        .value as OmvSharePrivilege["permission"];
                      setPrivilegeDesired((current) =>
                        current
                          ? {
                              ...current,
                              permission,
                            }
                          : current,
                      );
                      setPrivilegePlan(null);
                    }}
                    className="mt-1.5 h-9 w-full rounded-xl border border-amber-100 bg-white px-3 text-xs outline-none focus:border-amber-500"
                  >
                    <option value="inherit">未单独设置</option>
                    <option value="none">禁止访问</option>
                    <option value="read">只读</option>
                    <option value="readWrite">读写</option>
                  </select>
                </label>
              </div>

              <div className="mt-4 flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => void previewPrivilegeChange()}
                  disabled={privilegePlanning}
                  className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-amber-600 px-4 text-xs font-medium text-white transition hover:bg-amber-700 disabled:opacity-50"
                >
                  {privilegePlanning && (
                    <Loader2Icon className="size-3.5 animate-spin" />
                  )}
                  {privilegePlanning ? "正在预览…" : "预览权限变更"}
                </button>
                <span className="text-[10px] text-slate-500">
                  预览不会修改 OMV 或文件内容
                </span>
              </div>

              {privilegePlan && (
                <div className="mt-4 rounded-xl border border-amber-100 bg-white p-3">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <strong className="block text-xs text-slate-800">
                        {privilegePlan.operation === "none"
                          ? "当前已经符合期望状态"
                          : `将更新${privilegePlan.principal.type === "user" ? "用户" : "用户组"} ${privilegePlan.principal.name} 的访问权限`}
                      </strong>
                      <span className="mt-1 block text-[10px] text-slate-500">
                        只更新共享配置权限；文件系统 ACL 与现有文件权限保持不变
                      </span>
                    </div>
                    {privilegePlan.requiresApproval && (
                      <button
                        type="button"
                        onClick={() => setPrivilegeApprovalOpen(true)}
                        className="h-8 shrink-0 rounded-lg bg-amber-500 px-3 text-[11px] font-medium text-white hover:bg-amber-600"
                      >
                        管理员确认并应用权限
                      </button>
                    )}
                  </div>
                  {privilegePlan.changes.map((change) => (
                    <div
                      key={change.field}
                      className="mt-2 grid grid-cols-[90px_1fr_auto_1fr] items-center gap-2 rounded-lg bg-slate-50 px-2.5 py-2 text-[10px] text-slate-500"
                    >
                      <span className="font-medium text-slate-700">
                        共享访问
                      </span>
                      <span>{permissionLabel[change.before]}</span>
                      <span>→</span>
                      <span className="font-medium text-amber-700">
                        {permissionLabel[change.after]}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}

          <section className="mt-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <h2 className="text-[15px] font-semibold">共享文件夹</h2>
                <p className="mt-0.5 text-[11px] text-slate-400">
                  名称直接生成相对目录；不展开文件内容或绝对宿主路径
                </p>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[11px] text-slate-500">
                  {overview.sharedFolders.length} 个
                </span>
                {status?.capabilities?.includes(
                  "shared-folder.create.simple.v1",
                ) &&
                  overview.sharedFolderTargets.length > 0 && (
                    <button
                      type="button"
                      onClick={() => {
                        setFolderCreateOpen((open) => !open);
                        clearFolderPreview();
                      }}
                      className="inline-flex h-8 items-center gap-1 rounded-lg bg-cyan-600 px-3 text-[11px] font-medium text-white transition hover:bg-cyan-700"
                    >
                      <FolderPlusIcon className="size-3.5" />
                      新建共享文件夹
                    </button>
                  )}
              </div>
            </div>

            {status?.capabilities?.includes("shared-folder.create.simple.v1") &&
              overview.sharedFolderTargets.length === 0 && (
                <p className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-[11px] leading-5 text-amber-800">
                  当前没有可用于新建共享文件夹的已挂载可写卷。请先在 OMV
                  中准备文件系统并完成挂载。
                </p>
              )}

            {folderCreateOpen && (
              <div className="mb-4 rounded-xl border border-cyan-200 bg-cyan-50/70 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <strong className="text-xs text-slate-800">
                      在现有可写卷上创建
                    </strong>
                    <p className="mt-1 text-[10px] leading-5 text-slate-500">
                      Echo 只按名称创建同名相对目录，固定为 users 组可读写的
                      2770 权限；不提供任意路径、ACL、修改或删除。
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setFolderCreateOpen(false);
                      clearFolderPreview();
                    }}
                    className="text-[11px] text-slate-500 hover:text-slate-800"
                  >
                    关闭
                  </button>
                </div>
                <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <label className="text-[11px] font-medium text-slate-600">
                    目标卷
                    <select
                      value={folderMountRef}
                      onChange={(event) => {
                        setFolderMountRef(event.currentTarget.value);
                        clearFolderPreview();
                      }}
                      className="mt-1.5 h-9 w-full rounded-xl border border-cyan-100 bg-white px-3 text-xs outline-none focus:border-cyan-500"
                    >
                      {overview.sharedFolderTargets.map((target) => (
                        <option
                          key={target.mountPointRef}
                          value={target.mountPointRef}
                        >
                          {target.label || "未命名卷"} · {target.type} · 可用{" "}
                          {Math.floor(
                            target.availableBytes / GIB_BYTES,
                          ).toLocaleString("zh-CN")}{" "}
                          GiB
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="text-[11px] font-medium text-slate-600">
                    文件夹名称
                    <input
                      value={folderName}
                      maxLength={64}
                      autoComplete="off"
                      onChange={(event) => {
                        setFolderName(event.currentTarget.value);
                        clearFolderPreview();
                      }}
                      className="mt-1.5 h-9 w-full rounded-xl border border-cyan-100 bg-white px-3 text-xs outline-none focus:border-cyan-500"
                      placeholder="例如 Family_Photos"
                    />
                  </label>
                </div>
                <label className="mt-3 block text-[11px] font-medium text-slate-600">
                  备注
                  <input
                    value={folderComment}
                    maxLength={512}
                    onChange={(event) => {
                      setFolderComment(event.currentTarget.value);
                      clearFolderPreview();
                    }}
                    className="mt-1.5 h-9 w-full rounded-xl border border-cyan-100 bg-white px-3 text-xs outline-none focus:border-cyan-500"
                    placeholder="这个共享文件夹用于什么"
                  />
                </label>
                <div className="mt-3 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void previewSharedFolder()}
                    disabled={
                      folderPlanning || !folderMountRef || !folderName.trim()
                    }
                    className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-cyan-600 px-4 text-xs font-medium text-white transition hover:bg-cyan-700 disabled:opacity-50"
                  >
                    {folderPlanning && (
                      <Loader2Icon className="size-3.5 animate-spin" />
                    )}
                    {folderPlanning ? "正在预览…" : "预览创建"}
                  </button>
                  <span className="text-[10px] text-slate-500">
                    预览不会创建目录
                  </span>
                </div>
                {folderPlan && (
                  <div className="mt-3 rounded-xl border border-cyan-100 bg-white p-3">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <strong className="block text-xs text-slate-800">
                          {folderPlan.operation === "create"
                            ? `将创建 ${folderPlan.desired.name}/`
                            : "同名共享文件夹已经符合要求"}
                        </strong>
                        <span className="mt-1 block text-[10px] text-slate-500">
                          {folderPlan.target.label || "未命名卷"} · 目录权限
                          2770 · users 组 · 不管理 ACL
                        </span>
                      </div>
                      {folderPlan.requiresApproval && (
                        <button
                          type="button"
                          onClick={() => setFolderApprovalOpen(true)}
                          className="h-8 shrink-0 rounded-lg bg-amber-500 px-3 text-[11px] font-medium text-white hover:bg-amber-600"
                        >
                          管理员确认并创建
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}
            <div className="space-y-2">
              {overview.sharedFolders.map((folder) => {
                const entries = privileges[folder.uuid];
                const smbRule = overview.smb.shares.find(
                  (share) => share.sharedFolderRef === folder.uuid,
                );
                const nfsRule = overview.nfs.shares.find(
                  (share) => share.sharedFolderRef === folder.uuid,
                );
                const canControlSmb =
                  overview.smb.enabled &&
                  status?.capabilities?.includes("smb.share.desired.v1");
                const canControlNfs =
                  overview.nfs.enabled &&
                  status?.capabilities?.includes(
                    "nfs.share.private-network.v1",
                  );
                const canControlPrivileges = status?.capabilities?.includes(
                  "shared-folder.privilege.simple.v1",
                );
                return (
                  <article
                    key={folder.uuid}
                    className="rounded-xl border border-slate-200 bg-slate-50 p-3"
                  >
                    <div className="flex items-start gap-3">
                      <FolderKeyIcon className="mt-0.5 size-4 shrink-0 text-blue-500" />
                      <div className="min-w-0 flex-1">
                        <strong className="block truncate text-xs text-slate-800">
                          {folder.name}
                        </strong>
                        <span className="mt-0.5 block truncate text-[10px] text-slate-400">
                          {folder.relativePath || "/"} ·{" "}
                          {folder.device || "设备未知"}
                        </span>
                      </div>
                      <span
                        className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                          folder.status === "OK"
                            ? "bg-emerald-100 text-emerald-700"
                            : "bg-amber-100 text-amber-800"
                        }`}
                      >
                        {folder.status}
                      </span>
                      <span className="rounded-full bg-white px-2 py-0.5 text-[10px] text-slate-500 ring-1 ring-slate-200">
                        {folder.supportsAcl ? "支持 POSIX ACL" : "基础权限"}
                      </span>
                      {canControlSmb && (
                        <button
                          type="button"
                          onClick={() => beginSmbControl(folder)}
                          className="inline-flex h-7 items-center gap-1 rounded-lg bg-blue-600 px-2.5 text-[10px] font-medium text-white transition hover:bg-blue-700"
                        >
                          <SlidersHorizontalIcon className="size-3" />
                          {smbRule ? "管理 SMB" : "启用 SMB"}
                        </button>
                      )}
                      {canControlNfs && (
                        <button
                          type="button"
                          onClick={() => beginNfsControl(folder)}
                          className="inline-flex h-7 items-center gap-1 rounded-lg bg-emerald-600 px-2.5 text-[10px] font-medium text-white transition hover:bg-emerald-700"
                        >
                          <SlidersHorizontalIcon className="size-3" />
                          {nfsRule ? "管理 NFS" : "启用 NFS"}
                        </button>
                      )}
                    </div>
                    {entries ? (
                      <div className="mt-2 flex flex-wrap gap-1.5 border-t border-slate-200 pt-2">
                        {entries.map((entry) => (
                          <span
                            key={`${entry.type}:${entry.id}`}
                            className="rounded-md bg-white px-2 py-1 text-[10px] text-slate-600 ring-1 ring-slate-200"
                          >
                            {entry.type === "user" ? "用户" : "组"} {entry.name}{" "}
                            · {permissionLabel[entry.permission]}
                          </span>
                        ))}
                        {entries.length === 0 && (
                          <span className="text-[10px] text-slate-400">
                            没有可管理的用户或组
                          </span>
                        )}
                        {canControlPrivileges && entries.length > 0 && (
                          <button
                            type="button"
                            onClick={() => void beginPrivilegeControl(folder)}
                            className="inline-flex items-center gap-1 rounded-md bg-amber-100 px-2 py-1 text-[10px] font-medium text-amber-800 hover:bg-amber-200"
                          >
                            <SlidersHorizontalIcon className="size-3" />
                            管理权限
                          </button>
                        )}
                      </div>
                    ) : (
                      <button
                        type="button"
                        disabled={privilegeLoading === folder.uuid}
                        onClick={() => void beginPrivilegeControl(folder)}
                        className="mt-2 inline-flex items-center gap-1 text-[10px] font-medium text-blue-600 disabled:text-slate-400"
                      >
                        {privilegeLoading === folder.uuid ? (
                          <Loader2Icon className="size-3 animate-spin" />
                        ) : (
                          <ShieldCheckIcon className="size-3" />
                        )}
                        {privilegeLoading === folder.uuid
                          ? "正在读取…"
                          : canControlPrivileges
                            ? "管理用户/组权限"
                            : "查看用户/组权限"}
                      </button>
                    )}
                  </article>
                );
              })}
            </div>
          </section>

          <section className="mt-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <h2 className="inline-flex items-center gap-2 text-[15px] font-semibold">
                  <UsersRoundIcon className="size-4 text-blue-500" />
                  家庭成员与用户组
                </h2>
                <p className="mt-0.5 text-[11px] text-slate-400">
                  {overview.users.length} 位成员 · {overview.groups.length} 个组
                </p>
              </div>
              <div className="flex items-center gap-2">
                {status?.capabilities?.includes("account.group.create.v1") && (
                  <button
                    type="button"
                    onClick={() => {
                      setAccountMode("group");
                      clearGroupPreview();
                      clearUserPreviewSecret();
                      clearPasswordResetSecret();
                      clearEchoLinkSecret();
                    }}
                    className="inline-flex h-8 items-center gap-1 rounded-lg border border-blue-200 bg-blue-50 px-3 text-[11px] font-medium text-blue-700 transition hover:bg-blue-100"
                  >
                    <UsersRoundIcon className="size-3.5" />
                    新建用户组
                  </button>
                )}
                {status?.capabilities?.includes("account.user.create.v1") && (
                  <button
                    type="button"
                    onClick={() => {
                      setAccountMode("user");
                      clearGroupPreview();
                      clearUserPreviewSecret();
                      clearPasswordResetSecret();
                      clearEchoLinkSecret();
                    }}
                    className="inline-flex h-8 items-center gap-1 rounded-lg bg-blue-600 px-3 text-[11px] font-medium text-white transition hover:bg-blue-700"
                  >
                    <UserPlusIcon className="size-3.5" />
                    添加家庭成员
                  </button>
                )}
              </div>
            </div>

            {accountMode === "group" && (
              <div className="mb-4 rounded-xl border border-blue-200 bg-blue-50/70 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <strong className="text-xs text-slate-800">
                      创建空用户组
                    </strong>
                    <p className="mt-1 text-[10px] leading-5 text-slate-500">
                      只创建普通 OMV
                      用户组，初始成员为空；不允许系统组、修改或删除已有组。
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setAccountMode(null);
                      clearGroupPreview();
                    }}
                    className="text-[11px] text-slate-500 hover:text-slate-800"
                  >
                    关闭
                  </button>
                </div>
                <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <label className="text-[11px] font-medium text-slate-600">
                    用户组名称
                    <input
                      value={groupName}
                      maxLength={32}
                      autoCapitalize="none"
                      autoComplete="off"
                      spellCheck={false}
                      onChange={(event) => {
                        setGroupName(event.currentTarget.value);
                        clearGroupPreview();
                      }}
                      className="mt-1.5 h-9 w-full rounded-xl border border-blue-100 bg-white px-3 text-xs outline-none focus:border-blue-500"
                      placeholder="例如 family"
                    />
                  </label>
                  <label className="text-[11px] font-medium text-slate-600">
                    用户组备注
                    <input
                      value={groupComment}
                      maxLength={65}
                      onChange={(event) => {
                        setGroupComment(event.currentTarget.value);
                        clearGroupPreview();
                      }}
                      className="mt-1.5 h-9 w-full rounded-xl border border-blue-100 bg-white px-3 text-xs outline-none focus:border-blue-500"
                      placeholder="例如 家庭成员"
                    />
                  </label>
                </div>
                <div className="mt-3 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void previewGroup()}
                    disabled={groupPlanning || !groupName.trim()}
                    className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-blue-600 px-4 text-xs font-medium text-white transition hover:bg-blue-700 disabled:opacity-50"
                  >
                    {groupPlanning && (
                      <Loader2Icon className="size-3.5 animate-spin" />
                    )}
                    {groupPlanning ? "正在预览…" : "预览用户组创建"}
                  </button>
                  <span className="text-[10px] text-slate-500">
                    预览不会修改 OMV
                  </span>
                </div>
                {groupPlan && (
                  <div className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-blue-100 bg-white p-3">
                    <div>
                      <strong className="block text-xs text-slate-800">
                        将创建空用户组 {groupPlan.desired.name}
                      </strong>
                      <span className="mt-1 block text-[10px] text-slate-500">
                        系统组不可选 · 不修改已有组 · 创建后回读验证
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => setGroupApprovalOpen(true)}
                      className="h-8 shrink-0 rounded-lg bg-amber-500 px-3 text-[11px] font-medium text-white hover:bg-amber-600"
                    >
                      管理员确认并创建组
                    </button>
                  </div>
                )}
              </div>
            )}

            {accountMode === "user" && (
              <div className="mb-4 rounded-xl border border-indigo-200 bg-indigo-50/70 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <strong className="text-xs text-slate-800">
                      添加家庭成员账号
                    </strong>
                    <p className="mt-1 text-[10px] leading-5 text-slate-500">
                      新账号禁用命令行登录和 SSH
                      密钥，不设置邮箱；成员密码只用于本次创建，不写入浏览器存储、计划结果或审计。
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={closeUserEditor}
                    className="text-[11px] text-slate-500 hover:text-slate-800"
                  >
                    关闭
                  </button>
                </div>
                <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <label className="text-[11px] font-medium text-slate-600">
                    成员账号
                    <input
                      value={userName}
                      maxLength={32}
                      autoCapitalize="none"
                      autoComplete="off"
                      spellCheck={false}
                      onChange={(event) => {
                        setUserName(event.currentTarget.value);
                        setUserPlan(null);
                        setUserDesired(null);
                      }}
                      className="mt-1.5 h-9 w-full rounded-xl border border-indigo-100 bg-white px-3 text-xs outline-none focus:border-indigo-500"
                      placeholder="例如 mother"
                    />
                  </label>
                  <label className="text-[11px] font-medium text-slate-600">
                    显示名称
                    <input
                      value={userDisplayName}
                      maxLength={65}
                      autoComplete="off"
                      onChange={(event) => {
                        setUserDisplayName(event.currentTarget.value);
                        setUserPlan(null);
                        setUserDesired(null);
                      }}
                      className="mt-1.5 h-9 w-full rounded-xl border border-indigo-100 bg-white px-3 text-xs outline-none focus:border-indigo-500"
                      placeholder="例如 妈妈"
                    />
                  </label>
                  <label className="text-[11px] font-medium text-slate-600">
                    成员密码
                    <input
                      type="password"
                      value={userPassword}
                      minLength={12}
                      maxLength={128}
                      autoComplete="new-password"
                      onChange={(event) => {
                        setUserPassword(event.currentTarget.value);
                        setUserPlan(null);
                        setUserDesired(null);
                      }}
                      className="mt-1.5 h-9 w-full rounded-xl border border-indigo-100 bg-white px-3 text-xs outline-none focus:border-indigo-500"
                      placeholder="至少 12 位强密码"
                    />
                  </label>
                  <label className="text-[11px] font-medium text-slate-600">
                    确认成员密码
                    <input
                      type="password"
                      value={userPasswordConfirm}
                      minLength={12}
                      maxLength={128}
                      autoComplete="new-password"
                      onChange={(event) => {
                        setUserPasswordConfirm(event.currentTarget.value);
                        setUserPlan(null);
                        setUserDesired(null);
                      }}
                      className="mt-1.5 h-9 w-full rounded-xl border border-indigo-100 bg-white px-3 text-xs outline-none focus:border-indigo-500"
                    />
                  </label>
                </div>
                {selectableUserGroups.length > 0 && (
                  <fieldset className="mt-3 rounded-xl border border-indigo-100 bg-white p-3">
                    <legend className="px-1 text-[11px] font-medium text-slate-600">
                      加入用户组
                    </legend>
                    <div className="flex flex-wrap gap-2">
                      {selectableUserGroups.map((name) => (
                        <label
                          key={name}
                          className="inline-flex items-center gap-1.5 rounded-lg bg-slate-50 px-2.5 py-1.5 text-[11px] text-slate-600"
                        >
                          <input
                            type="checkbox"
                            checked={userGroups.includes(name)}
                            onChange={(event) => {
                              const checked = event.currentTarget.checked;
                              setUserGroups((current) =>
                                checked
                                  ? [...current, name].sort()
                                  : current.filter((entry) => entry !== name),
                              );
                              setUserPlan(null);
                              setUserDesired(null);
                            }}
                          />
                          {name}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                )}
                <div className="mt-3 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void previewUser()}
                    disabled={
                      userPlanning ||
                      !userName.trim() ||
                      !userDisplayName.trim() ||
                      !userPassword ||
                      !userPasswordConfirm
                    }
                    className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-indigo-600 px-4 text-xs font-medium text-white transition hover:bg-indigo-700 disabled:opacity-50"
                  >
                    {userPlanning && (
                      <Loader2Icon className="size-3.5 animate-spin" />
                    )}
                    {userPlanning ? "正在预览…" : "预览成员创建"}
                  </button>
                  <span className="text-[10px] text-slate-500">
                    OMV 自动 home 开启时会安全拒绝
                  </span>
                </div>
                {userPlan && (
                  <div className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-indigo-100 bg-white p-3">
                    <div>
                      <strong className="block text-xs text-slate-800">
                        将创建家庭成员 {userPlan.desired.displayName}（
                        {userPlan.desired.name}）
                      </strong>
                      <span className="mt-1 block text-[10px] text-slate-500">
                        无命令行登录 · 无 SSH 密钥 · 密码已绑定计划且不会回传
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => setUserApprovalOpen(true)}
                      className="h-8 shrink-0 rounded-lg bg-amber-500 px-3 text-[11px] font-medium text-white hover:bg-amber-600"
                    >
                      管理员确认并创建成员
                    </button>
                  </div>
                )}
              </div>
            )}
            {accountMode === "echo" && (
              <div className="mb-4 rounded-xl border border-violet-200 bg-violet-50/70 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <strong className="text-xs text-slate-800">
                      开通 Echo 登录 · {echoMemberName}
                    </strong>
                    <p className="mt-1 text-[10px] leading-5 text-slate-500">
                      仅把这个已存在的 OMV 成员映射为 Echo 家庭账号。Echo
                      使用独立密码，不读取或复用 OMV 密码，也不会访问 Agent /
                      OMV 私有数据库。
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={closeEchoLink}
                    className="text-[11px] text-slate-500 hover:text-slate-800"
                  >
                    关闭
                  </button>
                </div>
                <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <label className="text-[11px] font-medium text-slate-600">
                    显示名称
                    <input
                      value={echoDisplayName}
                      maxLength={64}
                      autoComplete="off"
                      onChange={(event) => {
                        setEchoDisplayName(event.currentTarget.value);
                        setEchoLinkPlan(null);
                      }}
                      className="mt-1.5 h-9 w-full rounded-xl border border-violet-100 bg-white px-3 text-xs outline-none focus:border-violet-500"
                    />
                  </label>
                  <label className="text-[11px] font-medium text-slate-600">
                    Echo 用户名
                    <input
                      value={echoMemberName}
                      readOnly
                      className="mt-1.5 h-9 w-full rounded-xl border border-violet-100 bg-slate-50 px-3 text-xs text-slate-500 outline-none"
                    />
                  </label>
                  <label className="text-[11px] font-medium text-slate-600">
                    独立 Echo 密码
                    <input
                      type="password"
                      value={echoPassword}
                      minLength={12}
                      maxLength={72}
                      autoComplete="new-password"
                      onChange={(event) => {
                        setEchoPassword(event.currentTarget.value);
                        setEchoLinkPlan(null);
                      }}
                      className="mt-1.5 h-9 w-full rounded-xl border border-violet-100 bg-white px-3 text-xs outline-none focus:border-violet-500"
                      placeholder="不要与 NAS / SMB 密码相同"
                    />
                  </label>
                  <label className="text-[11px] font-medium text-slate-600">
                    确认 Echo 密码
                    <input
                      type="password"
                      value={echoPasswordConfirm}
                      minLength={12}
                      maxLength={72}
                      autoComplete="new-password"
                      onChange={(event) => {
                        setEchoPasswordConfirm(event.currentTarget.value);
                        setEchoLinkPlan(null);
                      }}
                      className="mt-1.5 h-9 w-full rounded-xl border border-violet-100 bg-white px-3 text-xs outline-none focus:border-violet-500"
                    />
                  </label>
                </div>
                <div className="mt-3 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void previewEchoLink()}
                    disabled={
                      echoLinkPlanning ||
                      !echoDisplayName.trim() ||
                      !echoPassword ||
                      !echoPasswordConfirm
                    }
                    className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-violet-600 px-4 text-xs font-medium text-white transition hover:bg-violet-700 disabled:opacity-50"
                  >
                    {echoLinkPlanning && (
                      <Loader2Icon className="size-3.5 animate-spin" />
                    )}
                    {echoLinkPlanning ? "正在预览…" : "预览登录开通"}
                  </button>
                  <span className="text-[10px] text-slate-500">
                    开通后可在锁屏页输入成员用户名
                  </span>
                </div>
                {echoLinkPlan && (
                  <div className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-violet-100 bg-white p-3">
                    <div>
                      <strong className="block text-xs text-slate-800">
                        将为 {echoLinkPlan.account.displayName} 开通 Echo 登录
                      </strong>
                      <span className="mt-1 block text-[10px] text-slate-500">
                        独立登录 · 独立 Agent 身份 · 关联 OMV 用户{" "}
                        {echoMemberName}
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => setEchoLinkApprovalOpen(true)}
                      className="h-8 shrink-0 rounded-lg bg-amber-500 px-3 text-[11px] font-medium text-white hover:bg-amber-600"
                    >
                      管理员确认并开通
                    </button>
                  </div>
                )}
              </div>
            )}
            {accountMode === "echoPassword" && (
              <div className="mb-4 rounded-xl border border-violet-200 bg-violet-50/70 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <strong className="text-xs text-slate-800">
                      重置 Echo 登录密码 · {echoLifecycleMember}
                    </strong>
                    <p className="mt-1 text-[10px] leading-5 text-slate-500">
                      只修改 Echo 登录密码，不修改 NAS / SMB
                      密码。成功后该成员现有网页和 WebSocket 会话会立即失效。
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setAccountMode(null);
                      setEchoReplacementPassword("");
                      setEchoReplacementConfirm("");
                      setEchoLifecyclePlan(null);
                    }}
                    className="text-[11px] text-slate-500 hover:text-slate-800"
                  >
                    关闭
                  </button>
                </div>
                <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <label className="text-[11px] font-medium text-slate-600">
                    新 Echo 密码
                    <input
                      aria-label="新 Echo 密码"
                      type="password"
                      value={echoReplacementPassword}
                      minLength={12}
                      maxLength={72}
                      autoComplete="new-password"
                      onChange={(event) => {
                        setEchoReplacementPassword(event.currentTarget.value);
                        setEchoLifecyclePlan(null);
                      }}
                      className="mt-1.5 h-9 w-full rounded-xl border border-violet-100 bg-white px-3 text-xs outline-none focus:border-violet-500"
                    />
                  </label>
                  <label className="text-[11px] font-medium text-slate-600">
                    确认新 Echo 密码
                    <input
                      aria-label="确认新 Echo 密码"
                      type="password"
                      value={echoReplacementConfirm}
                      minLength={12}
                      maxLength={72}
                      autoComplete="new-password"
                      onChange={(event) => {
                        setEchoReplacementConfirm(event.currentTarget.value);
                        setEchoLifecyclePlan(null);
                      }}
                      className="mt-1.5 h-9 w-full rounded-xl border border-violet-100 bg-white px-3 text-xs outline-none focus:border-violet-500"
                    />
                  </label>
                </div>
                <div className="mt-3 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void previewEchoPasswordReset()}
                    disabled={
                      echoLifecyclePlanning ||
                      !echoReplacementPassword ||
                      !echoReplacementConfirm
                    }
                    className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-violet-600 px-4 text-xs font-medium text-white transition hover:bg-violet-700 disabled:opacity-50"
                  >
                    {echoLifecyclePlanning ? "正在预览…" : "预览 Echo 密码重置"}
                  </button>
                  {echoLifecyclePlan?.operation === "resetMemberPassword" && (
                    <button
                      type="button"
                      onClick={() => setEchoPasswordApprovalOpen(true)}
                      className="h-9 rounded-xl bg-amber-500 px-3 text-[11px] font-medium text-white hover:bg-amber-600"
                    >
                      管理员确认并重置
                    </button>
                  )}
                </div>
              </div>
            )}
            {accountMode === "password" && (
              <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50/70 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <strong className="text-xs text-slate-800">
                      重置成员密码 · {passwordUserName}
                    </strong>
                    <p className="mt-1 text-[10px] leading-5 text-slate-500">
                      仅允许仍保持禁用命令行登录、无邮箱和无 SSH
                      密钥的受限家庭成员。新密码会绑定本次计划并经秘密宿主通道发送，不写入浏览器存储、响应或审计。
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={closePasswordReset}
                    className="text-[11px] text-slate-500 hover:text-slate-800"
                  >
                    关闭
                  </button>
                </div>
                <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <label className="text-[11px] font-medium text-slate-600">
                    新密码
                    <input
                      type="password"
                      value={replacementPassword}
                      minLength={12}
                      maxLength={128}
                      autoComplete="new-password"
                      onChange={(event) => {
                        setReplacementPassword(event.currentTarget.value);
                        setPasswordDesired(null);
                        setPasswordPlan(null);
                      }}
                      className="mt-1.5 h-9 w-full rounded-xl border border-amber-100 bg-white px-3 text-xs outline-none focus:border-amber-500"
                      placeholder="至少 12 位强密码"
                    />
                  </label>
                  <label className="text-[11px] font-medium text-slate-600">
                    确认新密码
                    <input
                      type="password"
                      value={replacementPasswordConfirm}
                      minLength={12}
                      maxLength={128}
                      autoComplete="new-password"
                      onChange={(event) => {
                        setReplacementPasswordConfirm(
                          event.currentTarget.value,
                        );
                        setPasswordDesired(null);
                        setPasswordPlan(null);
                      }}
                      className="mt-1.5 h-9 w-full rounded-xl border border-amber-100 bg-white px-3 text-xs outline-none focus:border-amber-500"
                    />
                  </label>
                </div>
                <div className="mt-3 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void previewPasswordReset()}
                    disabled={
                      passwordPlanning ||
                      !replacementPassword ||
                      !replacementPasswordConfirm
                    }
                    className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-amber-600 px-4 text-xs font-medium text-white transition hover:bg-amber-700 disabled:opacity-50"
                  >
                    {passwordPlanning && (
                      <Loader2Icon className="size-3.5 animate-spin" />
                    )}
                    {passwordPlanning ? "正在预览…" : "预览密码重置"}
                  </button>
                  <span className="text-[10px] text-slate-500">
                    密码变更成功后无法由 Echo 自动恢复旧密码
                  </span>
                </div>
                {passwordPlan && (
                  <div className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-amber-100 bg-white p-3">
                    <div>
                      <strong className="block text-xs text-slate-800">
                        将替换 {passwordPlan.desired.name} 的 SMB / NAS 密码
                      </strong>
                      <span className="mt-1 block text-[10px] text-slate-500">
                        账号属性保持并回读验证 · 新密码已绑定计划且不会回传
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => setPasswordApprovalOpen(true)}
                      className="h-8 shrink-0 rounded-lg bg-amber-500 px-3 text-[11px] font-medium text-white hover:bg-amber-600"
                    >
                      管理员确认并重置
                    </button>
                  </div>
                )}
              </div>
            )}
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {overview.users.map((user) => {
                const echoAccount = echoAccounts?.accounts.find(
                  (account) => account.omvUsername === user.name,
                );
                return (
                  <article
                    key={user.uid}
                    className="flex items-center gap-2.5 rounded-xl bg-slate-50 p-3"
                  >
                    <span className="grid size-8 place-items-center rounded-full bg-blue-100 text-blue-700">
                      <UserRoundIcon className="size-4" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <strong className="block truncate text-xs">
                        {user.name}
                      </strong>
                      <small className="block truncate text-[10px] text-slate-400">
                        {user.comment || `UID ${user.uid}`} ·{" "}
                        {user.groups.join(", ") || "无附加组"}
                      </small>
                    </span>
                    {echoAccount ? (
                      <>
                        <span
                          className={`shrink-0 rounded-lg px-2 py-1 text-[10px] font-medium ${
                            echoAccount.active
                              ? "bg-violet-100 text-violet-700"
                              : "bg-slate-200 text-slate-600"
                          }`}
                        >
                          {echoAccount.active ? "Echo 已启用" : "Echo 已停用"}
                        </span>
                        {echoAccounts?.canManage && (
                          <>
                            <button
                              type="button"
                              disabled={echoLifecyclePlanning}
                              onClick={() =>
                                void previewEchoStatus(
                                  echoAccount.username,
                                  !echoAccount.active,
                                )
                              }
                              className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 py-1 text-[10px] font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                            >
                              {echoAccount.active ? "停用 Echo" : "启用 Echo"}
                            </button>
                            <button
                              type="button"
                              onClick={() =>
                                beginEchoPasswordReset(echoAccount.username)
                              }
                              className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-violet-200 bg-white px-2 py-1 text-[10px] font-medium text-violet-700 hover:bg-violet-50"
                            >
                              <KeyRoundIcon className="size-3" />
                              重置 Echo 密码
                            </button>
                            {!echoAccount.active && (
                              <button
                                type="button"
                                disabled={echoLifecyclePlanning}
                                onClick={() =>
                                  void previewEchoUnlink(echoAccount.username)
                                }
                                className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-red-200 bg-white px-2 py-1 text-[10px] font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
                              >
                                移除 Echo 登录
                              </button>
                            )}
                          </>
                        )}
                      </>
                    ) : (
                      echoAccounts?.canManage && (
                        <button
                          type="button"
                          onClick={() =>
                            beginEchoLink(user.name, user.comment || user.name)
                          }
                          className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-violet-200 bg-white px-2 py-1 text-[10px] font-medium text-violet-700 hover:bg-violet-50"
                        >
                          <ShieldCheckIcon className="size-3" />
                          开通 Echo
                        </button>
                      )
                    )}
                    {status?.capabilities?.includes(
                      "account.user.password.reset.v1",
                    ) && (
                      <button
                        type="button"
                        onClick={() => beginPasswordReset(user.name)}
                        className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-amber-200 bg-white px-2 py-1 text-[10px] font-medium text-amber-700 hover:bg-amber-50"
                      >
                        <KeyRoundIcon className="size-3" />
                        重置密码
                      </button>
                    )}
                  </article>
                );
              })}
            </div>
          </section>
        </>
      )}
      <HighRiskApprovalDialog
        open={echoLinkApprovalOpen && Boolean(echoLinkPlan)}
        title="开通 Echo 家庭登录"
        description="Echo 只会为这个现有 OMV 成员创建独立的本地登录和 Agent 身份映射；不会读取或复用 NAS / SMB 密码，也不会修改 OMV 用户。"
        targetLabel={
          echoLinkPlan
            ? `${echoLinkPlan.account.displayName} · ${echoLinkPlan.account.username} · ${echoLinkPlan.planId.slice(0, 12)}`
            : undefined
        }
        confirmLabel="确认开通 Echo 登录"
        onCancel={() => {
          setEchoLinkApprovalOpen(false);
          clearEchoLinkSecret();
        }}
        onConfirm={confirmEchoLink}
      />
      <HighRiskApprovalDialog
        open={echoStatusApprovalOpen && Boolean(echoStatusDesired)}
        title={
          echoStatusDesired?.active
            ? "启用 Echo 家庭登录"
            : "停用 Echo 家庭登录"
        }
        description={
          echoStatusDesired?.active
            ? "重新开放该成员的 Echo 登录；之前签发的会话仍保持失效，需要使用现有 Echo 密码重新登录。"
            : "立即阻止该成员继续登录 Echo，并撤销其现有网页、Bearer 和 WebSocket 会话；不会删除 OMV 用户或 NAS 数据。"
        }
        targetLabel={
          echoLifecyclePlan
            ? `${echoLifecycleMember} · ${echoLifecyclePlan.planId.slice(0, 12)}`
            : undefined
        }
        confirmLabel={
          echoStatusDesired?.active ? "确认启用 Echo" : "确认停用 Echo"
        }
        onCancel={() => {
          setEchoStatusApprovalOpen(false);
          setEchoStatusDesired(null);
          setEchoLifecyclePlan(null);
        }}
        onConfirm={confirmEchoStatus}
      />
      <HighRiskApprovalDialog
        open={echoPasswordApprovalOpen && Boolean(echoPasswordDesired)}
        title="重置 Echo 家庭登录密码"
        description="只替换该成员的 Echo 登录密码并立即撤销其现有会话；不会修改或读取 OMV、SMB 密码，密码不会出现在计划、响应或审计中。"
        targetLabel={
          echoLifecyclePlan
            ? `${echoLifecycleMember} · ${echoLifecyclePlan.planId.slice(0, 12)}`
            : undefined
        }
        confirmLabel="确认重置 Echo 密码"
        onCancel={() => {
          setEchoPasswordApprovalOpen(false);
          setEchoReplacementPassword("");
          setEchoReplacementConfirm("");
          setEchoPasswordDesired(null);
          setEchoLifecyclePlan(null);
        }}
        onConfirm={confirmEchoPasswordReset}
      />
      <HighRiskApprovalDialog
        open={echoUnlinkApprovalOpen && Boolean(echoUnlinkDesired)}
        title="移除 Echo 家庭登录"
        description="移除已停用成员的 Echo 本地登录和 Agent 身份映射。OMV 用户、SMB 密码、共享权限及 NAS 文件不会删除；以后可重新开通并设置新的 Echo 密码。"
        targetLabel={
          echoLifecyclePlan
            ? `${echoLifecycleMember} · ${echoLifecyclePlan.planId.slice(0, 12)}`
            : undefined
        }
        confirmLabel="确认移除 Echo 登录"
        onCancel={() => {
          setEchoUnlinkApprovalOpen(false);
          setEchoUnlinkDesired(null);
          setEchoLifecyclePlan(null);
        }}
        onConfirm={confirmEchoUnlink}
      />
      <HighRiskApprovalDialog
        open={groupApprovalOpen && Boolean(groupPlan)}
        title="创建 NAS 用户组"
        description="Echo 将创建一个初始成员为空的普通 OMV 用户组并回读验证；不会修改系统组、已有组或文件权限。失败时只回滚本次尚未使用的新组。"
        targetLabel={
          groupPlan
            ? `${groupPlan.desired.name} · ${groupPlan.planId.slice(0, 12)}`
            : undefined
        }
        confirmLabel="确认创建用户组"
        onCancel={() => setGroupApprovalOpen(false)}
        onConfirm={confirmGroup}
      />
      <HighRiskApprovalDialog
        open={userApprovalOpen && Boolean(userPlan)}
        title="创建家庭成员账号"
        description="Echo 将创建禁用命令行登录、无 SSH 密钥和无邮箱的普通 OMV 用户，并加入预览中的普通组。成员密码只用于本次创建，不会出现在计划、响应或审计；取消或失败后会从界面清除。"
        targetLabel={
          userPlan
            ? `${userPlan.desired.displayName} · ${userPlan.desired.name} · ${userPlan.planId.slice(0, 12)}`
            : undefined
        }
        confirmLabel="确认创建家庭成员"
        onCancel={() => {
          setUserApprovalOpen(false);
          clearUserPreviewSecret();
        }}
        onConfirm={confirmUser}
      />
      <HighRiskApprovalDialog
        open={passwordApprovalOpen && Boolean(passwordPlan)}
        title="重置家庭成员密码"
        description="Echo 将通过秘密宿主通道替换这个受限 OMV 用户的系统与 SMB 密码，并回读确认账号、用户组、nologin、邮箱和 SSH 边界没有变化。密码一旦被 OMV 接受便无法自动恢复旧值；结果不确定时请使用新密码重新预览并验证登录。"
        targetLabel={
          passwordPlan
            ? `${passwordPlan.desired.name} · ${passwordPlan.planId.slice(0, 12)}`
            : undefined
        }
        confirmLabel="确认重置成员密码"
        onCancel={() => {
          setPasswordApprovalOpen(false);
          clearPasswordResetSecret();
        }}
        onConfirm={confirmPasswordReset}
      />
      <HighRiskApprovalDialog
        open={privilegeApprovalOpen && Boolean(privilegePlan)}
        title="应用共享访问权限"
        description="Echo 只会更新所选已有用户或组在这个共享文件夹上的 OMV 服务权限，并按需部署 Samba/Rsync 配置；不会修改文件系统 ACL、递归权限或文件内容。失败时会恢复原权限并回读验证。"
        targetLabel={
          privilegePlan
            ? [
                privilegePlan.sharedFolder.name,
                privilegePlan.principal.name,
                `${permissionLabel[privilegePlan.principal.before]} → ${permissionLabel[privilegePlan.principal.after]}`,
                privilegePlan.planId.slice(0, 12),
              ].join(" · ")
            : undefined
        }
        confirmLabel="确认应用权限"
        onCancel={() => setPrivilegeApprovalOpen(false)}
        onConfirm={confirmPrivilegeChange}
      />
      <HighRiskApprovalDialog
        open={folderApprovalOpen && Boolean(folderPlan)}
        title="创建共享文件夹"
        description="Echo 将在所选 OMV 可写卷上按名称创建同名相对目录，固定使用 users 组 2770 权限并回读验证。失败回滚只移除 OMV 配置，不删除目录或其中的数据。"
        targetLabel={
          folderPlan
            ? `${folderPlan.target.label || "未命名卷"} · ${folderPlan.desired.name}/ · ${folderPlan.planId.slice(0, 12)}`
            : undefined
        }
        confirmLabel="确认创建"
        onCancel={() => setFolderApprovalOpen(false)}
        onConfirm={confirmSharedFolder}
      />
      <HighRiskApprovalDialog
        open={approvalOpen && Boolean(plan)}
        title="应用 SMB 配置"
        description="Echo 将把刚才预览的私有 SMB 期望状态交给 OMV 同步部署，并在失败时尝试恢复原规则。"
        targetLabel={
          editingFolder
            ? `${editingFolder.name} · ${plan?.planId.slice(0, 12)}`
            : undefined
        }
        confirmLabel="确认应用"
        onCancel={() => setApprovalOpen(false)}
        onConfirm={confirmSmbChange}
      />
      <HighRiskApprovalDialog
        open={quotaApprovalOpen && Boolean(quotaPlan)}
        title="应用文件系统硬配额"
        description="Echo 将把刚才预览的用户或组硬限制交给 OMV 部署，回读验证失败时会尝试恢复原配额。该限制按文件所有者覆盖本机、SMB 和 NFS。"
        targetLabel={
          quotaPlan
            ? [
                quotaPlan.filesystem.label || "未命名卷",
                quotaPlan.subject.name,
                quotaPlan.planId.slice(0, 12),
              ].join(" · ")
            : undefined
        }
        confirmLabel="确认应用配额"
        onCancel={() => setQuotaApprovalOpen(false)}
        onConfirm={confirmQuotaChange}
      />
      <HighRiskApprovalDialog
        open={nfsApprovalOpen && Boolean(nfsPlan)}
        title="应用 NFS 私网规则"
        description="Echo 将把刚才预览的私网 NFS 规则交给 OMV 同步部署，强制 root_squash 和同步写入；回读验证失败时会尝试恢复原规则。"
        targetLabel={
          editingNfsFolder && nfsPlan
            ? `${editingNfsFolder.name} · ${nfsPlan.desired.clientCidr} · ${nfsPlan.planId.slice(0, 12)}`
            : undefined
        }
        confirmLabel="确认应用 NFS"
        onCancel={() => setNfsApprovalOpen(false)}
        onConfirm={confirmNfsChange}
      />
    </>
  );
}
