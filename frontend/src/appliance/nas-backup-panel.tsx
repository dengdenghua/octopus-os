import { useCallback, useEffect, useState } from "react";
import {
  ArchiveRestoreIcon,
  CheckCircle2Icon,
  Loader2Icon,
  ShieldAlertIcon,
} from "lucide-react";

import { requestHighRiskApproval } from "@/appliance/approval";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";
import { NasBackupRemotePanel } from "@/appliance/nas-backup-remote-panel";
import { NasBackupRestorePanel } from "@/appliance/nas-backup-restore-panel";
import {
  applyNasBackupCredential,
  applyNasBackupCredentialRotation,
  applyNasBackupSchedule,
  fetchNasBackupRepositoryCandidates,
  fetchNasBackupSchedule,
  planNasBackupCredential,
  planNasBackupCredentialRotation,
  planNasBackupSchedule,
  type NasBackupCredentialDesired,
  type NasBackupCredentialPlan,
  type NasBackupCredentialRotationDesired,
  type NasBackupCredentialRotationPlan,
  type NasBackupRepositoryCandidate,
  type NasBackupScheduleDesired,
  type NasBackupSchedulePlan,
  type NasBackupScheduleStatus,
} from "@/appliance/nas-backup";

function outcomeLabel(outcome: "completed" | "disabled" | "failed") {
  if (outcome === "completed") return "成功";
  if (outcome === "disabled") return "已跳过";
  return "失败";
}

function capacityLabel(bytes: number) {
  if (bytes >= 1024 ** 4) return `${(bytes / 1024 ** 4).toFixed(1)} TiB`;
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(0)} MiB`;
  return `${bytes} B`;
}

export function NasBackupPanel() {
  const [status, setStatus] = useState<NasBackupScheduleStatus | null>(null);
  const [repositoryMount, setRepositoryMount] = useState("");
  const [repository, setRepository] = useState("");
  const [repositoryCandidates, setRepositoryCandidates] = useState<
    NasBackupRepositoryCandidate[]
  >([]);
  const [candidateLoading, setCandidateLoading] = useState(true);
  const [candidateError, setCandidateError] = useState(false);
  const [credentialMode, setCredentialMode] = useState<
    "initialize" | "connect"
  >("initialize");
  const [backupPassword, setBackupPassword] = useState("");
  const [backupPasswordConfirmation, setBackupPasswordConfirmation] =
    useState("");
  const [currentBackupPassword, setCurrentBackupPassword] = useState("");
  const [newBackupPassword, setNewBackupPassword] = useState("");
  const [newBackupPasswordConfirmation, setNewBackupPasswordConfirmation] =
    useState("");
  const [pendingCredentialDesired, setPendingCredentialDesired] =
    useState<NasBackupCredentialDesired | null>(null);
  const [pendingCredentialPlan, setPendingCredentialPlan] =
    useState<NasBackupCredentialPlan | null>(null);
  const [pendingRotationDesired, setPendingRotationDesired] =
    useState<NasBackupCredentialRotationDesired | null>(null);
  const [pendingRotationPlan, setPendingRotationPlan] =
    useState<NasBackupCredentialRotationPlan | null>(null);
  const [pendingDesired, setPendingDesired] =
    useState<NasBackupScheduleDesired | null>(null);
  const [pendingPlan, setPendingPlan] = useState<NasBackupSchedulePlan | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setStatus(await fetchNasBackupSchedule());
    } catch (reason) {
      setStatus(null);
      setError(
        reason instanceof Error ? reason.message : "无法读取 NAS 备份状态",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const refreshCandidates = useCallback(async () => {
    setCandidateLoading(true);
    try {
      const result = await fetchNasBackupRepositoryCandidates();
      setRepositoryCandidates(result.candidates);
      setCandidateError(false);
    } catch {
      setRepositoryCandidates([]);
      setCandidateError(true);
    } finally {
      setCandidateLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshCandidates();
  }, [refreshCandidates]);

  const selectRepositoryCandidate = (mountpoint: string) => {
    if (!mountpoint) return;
    setRepositoryMount(mountpoint);
    setRepository(`${mountpoint}/echo-restic`);
  };

  const remoteChanged = async (mountpoint?: string) => {
    await refreshCandidates();
    if (mountpoint) selectRepositoryCandidate(mountpoint);
  };

  const preview = async (enabled: boolean) => {
    const mount = repositoryMount.trim();
    const destination = repository.trim();
    if (enabled && (!mount.startsWith("/") || !destination.startsWith("/"))) {
      setError("启用前请填写绝对路径形式的外部备份盘挂载点和仓库目录");
      return;
    }
    const desired: NasBackupScheduleDesired = {
      schema: "echo.nas-data-backup-schedule.v1",
      enabled,
      repository: enabled ? destination : null,
      repositoryMount: enabled ? mount : null,
    };
    setBusy(true);
    setError(null);
    try {
      const plan = await planNasBackupSchedule(desired);
      if (plan.operation === "none") {
        await refresh();
        return;
      }
      setPendingDesired(desired);
      setPendingPlan(plan);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法生成 NAS 备份策略预览",
      );
    } finally {
      setBusy(false);
    }
  };

  const previewCredential = async () => {
    const mount = repositoryMount.trim();
    const destination = repository.trim();
    if (!mount.startsWith("/") || !destination.startsWith("/")) {
      setError("请填写绝对路径形式的外部备份盘挂载点和仓库目录");
      return;
    }
    if (backupPassword !== backupPasswordConfirmation) {
      setError("两次输入的仓库密码不一致");
      return;
    }
    if (new TextEncoder().encode(backupPassword).length < 12) {
      setError("仓库密码至少需要 12 个 UTF-8 字节");
      return;
    }
    const desired: NasBackupCredentialDesired = {
      schema: "echo.nas-data-backup-credential-desired.v1",
      mode: credentialMode,
      repository: destination,
      repositoryMount: mount,
      password: backupPassword,
    };
    setBusy(true);
    setError(null);
    try {
      setPendingCredentialDesired(desired);
      setPendingCredentialPlan(await planNasBackupCredential(desired));
    } catch (reason) {
      setPendingCredentialDesired(null);
      setError(
        reason instanceof Error ? reason.message : "无法生成 NAS 备份凭据预览",
      );
    } finally {
      setBusy(false);
    }
  };

  const apply = async (password: string) => {
    if (!pendingDesired || !pendingPlan) return;
    setBusy(true);
    setError(null);
    try {
      const approval = await requestHighRiskApproval(
        "storage.nas-backup.schedule",
        pendingPlan.planId,
        password,
      );
      await applyNasBackupSchedule(
        pendingDesired,
        pendingPlan.planId,
        approval.approvalToken,
      );
      setPendingDesired(null);
      setPendingPlan(null);
      if (!pendingDesired.enabled) {
        setRepositoryMount("");
        setRepository("");
      }
      await refresh();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法更新 NAS 备份策略",
      );
      throw reason;
    } finally {
      setBusy(false);
    }
  };

  const applyCredential = async (administratorPassword: string) => {
    if (!pendingCredentialDesired || !pendingCredentialPlan) return;
    setBusy(true);
    setError(null);
    try {
      const approval = await requestHighRiskApproval(
        "storage.nas-backup.credential.provision",
        pendingCredentialPlan.planId,
        administratorPassword,
      );
      await applyNasBackupCredential(
        pendingCredentialDesired,
        pendingCredentialPlan.planId,
        approval.approvalToken,
      );
      setPendingCredentialDesired(null);
      setPendingCredentialPlan(null);
      setBackupPassword("");
      setBackupPasswordConfirmation("");
      await refresh();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法配置 NAS 备份凭据",
      );
      throw reason;
    } finally {
      setBusy(false);
    }
  };

  const clearRotationSecrets = () => {
    setPendingRotationDesired(null);
    setPendingRotationPlan(null);
    setCurrentBackupPassword("");
    setNewBackupPassword("");
    setNewBackupPasswordConfirmation("");
  };

  const previewRotation = async () => {
    const mount = repositoryMount.trim();
    const destination = repository.trim();
    if (!mount.startsWith("/") || !destination.startsWith("/")) {
      setError("轮换前请填写当前外部备份盘挂载点和仓库绝对路径");
      return;
    }
    if (newBackupPassword !== newBackupPasswordConfirmation) {
      setError("两次输入的新仓库密码不一致");
      return;
    }
    if (
      new TextEncoder().encode(currentBackupPassword).length < 12 ||
      new TextEncoder().encode(newBackupPassword).length < 12
    ) {
      setError("当前密码和新密码都至少需要 12 个 UTF-8 字节");
      return;
    }
    if (currentBackupPassword === newBackupPassword) {
      setError("新仓库密码必须与当前密码不同");
      return;
    }
    const desired: NasBackupCredentialRotationDesired = {
      schema: "echo.nas-data-backup-credential-rotation-desired.v1",
      repository: destination,
      repositoryMount: mount,
      currentPassword: currentBackupPassword,
      newPassword: newBackupPassword,
    };
    setBusy(true);
    setError(null);
    try {
      setPendingRotationDesired(desired);
      setPendingRotationPlan(await planNasBackupCredentialRotation(desired));
    } catch (reason) {
      setPendingRotationDesired(null);
      setError(
        reason instanceof Error ? reason.message : "无法生成凭据轮换预览",
      );
    } finally {
      setBusy(false);
    }
  };

  const applyRotation = async (administratorPassword: string) => {
    if (!pendingRotationDesired || !pendingRotationPlan) return;
    setBusy(true);
    setError(null);
    try {
      const approval = await requestHighRiskApproval(
        "storage.nas-backup.credential.rotate",
        pendingRotationPlan.planId,
        administratorPassword,
      );
      await applyNasBackupCredentialRotation(
        pendingRotationDesired,
        pendingRotationPlan.planId,
        approval.approvalToken,
      );
      clearRotationSecrets();
      await refresh();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法轮换 NAS 备份凭据",
      );
      throw reason;
    } finally {
      setBusy(false);
    }
  };

  const ready =
    status?.schedulerInstalled &&
    status.credentialConfigured &&
    !status.credentialRotationRecoveryPending &&
    !loading;
  const recent = [...(status?.history ?? [])].reverse().slice(0, 3);

  return (
    <>
      <section className="mt-5 rounded-[22px] bg-white/80 p-5 shadow-sm ring-1 ring-white/90">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <ArchiveRestoreIcon className="size-5 text-blue-600" /> 异机 NAS
              数据备份
            </h2>
            <p className="mt-1 max-w-2xl text-[11px] leading-5 text-slate-500">
              每天 03:30 后备份同一批 Btrfs
              只读快照到独立挂载盘；仓库加密，并在完成后执行完整读取校验。路径不会出现在状态和审计记录中。
            </p>
          </div>
          <button
            type="button"
            disabled={
              loading ||
              busy ||
              !status ||
              status.credentialRotationRecoveryPending ||
              (!status.enabled && !ready)
            }
            onClick={() => status && void preview(!status.enabled)}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 text-[11px] font-semibold text-slate-700 disabled:opacity-40"
          >
            {(loading || busy) && (
              <Loader2Icon className="size-3 animate-spin" />
            )}
            {status?.enabled ? "停用每日备份" : "预览并启用"}
          </button>
        </div>

        {status && !status.enabled && (
          <>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              <label className="text-[11px] font-medium text-slate-700 md:col-span-2">
                已发现的安全外置挂载
                <select
                  aria-label="已发现的安全外置挂载"
                  defaultValue=""
                  disabled={
                    candidateLoading || repositoryCandidates.length === 0
                  }
                  onChange={(event) =>
                    selectRepositoryCandidate(event.target.value)
                  }
                  className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[11px] outline-none focus:border-blue-400 disabled:bg-slate-50"
                >
                  <option value="">
                    {candidateLoading
                      ? "正在扫描 USB、网络和 rclone 挂载…"
                      : repositoryCandidates.length
                        ? "选择一个已通过独立文件系统校验的挂载"
                        : "未发现候选；仍可在下方手动填写"}
                  </option>
                  {repositoryCandidates.map((candidate) => (
                    <option
                      key={candidate.mountpoint}
                      value={candidate.mountpoint}
                    >
                      {candidate.mountpoint} ·{" "}
                      {candidate.kind === "remote" ? "远端" : "本地外置"} ·{" "}
                      {candidate.filesystem} · 可用{" "}
                      {capacityLabel(candidate.freeBytes)}
                    </option>
                  ))}
                </select>
                <span className="mt-1 block font-normal leading-5 text-slate-500">
                  {candidateError
                    ? "自动发现暂不可用，不影响手动填写；提交时仍会重新校验挂载边界。"
                    : "只显示可写、独立于系统和 NAS 数据盘的挂载；不会展示远端源地址或凭据。"}
                </span>
              </label>
              <label className="text-[11px] font-medium text-slate-700">
                外部备份盘挂载点
                <input
                  aria-label="外部备份盘挂载点"
                  value={repositoryMount}
                  onChange={(event) => setRepositoryMount(event.target.value)}
                  placeholder="/mnt/backup"
                  className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 font-mono text-[11px] outline-none focus:border-blue-400"
                />
              </label>
              <label className="text-[11px] font-medium text-slate-700">
                Restic 仓库目录
                <input
                  aria-label="Restic 仓库目录"
                  value={repository}
                  onChange={(event) => setRepository(event.target.value)}
                  placeholder="/mnt/backup/echo-restic"
                  className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 font-mono text-[11px] outline-none focus:border-blue-400"
                />
              </label>
              {!status.credentialConfigured && (
                <>
                  <label className="text-[11px] font-medium text-slate-700">
                    仓库类型
                    <select
                      aria-label="仓库类型"
                      value={credentialMode}
                      onChange={(event) =>
                        setCredentialMode(
                          event.target.value as "initialize" | "connect",
                        )
                      }
                      className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[11px] outline-none focus:border-blue-400"
                    >
                      <option value="initialize">初始化空仓库</option>
                      <option value="connect">连接已有仓库</option>
                    </select>
                  </label>
                  <label className="text-[11px] font-medium text-slate-700">
                    仓库加密密码
                    <input
                      type="password"
                      autoComplete="new-password"
                      aria-label="仓库加密密码"
                      value={backupPassword}
                      onChange={(event) =>
                        setBackupPassword(event.target.value)
                      }
                      className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[11px] outline-none focus:border-blue-400"
                    />
                  </label>
                  <label className="text-[11px] font-medium text-slate-700">
                    再次输入仓库密码
                    <input
                      type="password"
                      autoComplete="new-password"
                      aria-label="再次输入仓库密码"
                      value={backupPasswordConfirmation}
                      onChange={(event) =>
                        setBackupPasswordConfirmation(event.target.value)
                      }
                      className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[11px] outline-none focus:border-blue-400"
                    />
                  </label>
                  <div className="flex items-end">
                    <button
                      type="button"
                      disabled={
                        busy ||
                        loading ||
                        status.credentialRotationRecoveryPending
                      }
                      onClick={() => void previewCredential()}
                      className="h-9 rounded-lg bg-blue-600 px-3 text-[11px] font-semibold text-white disabled:opacity-40"
                    >
                      {credentialMode === "initialize"
                        ? "初始化并加密保存"
                        : "验证并加密保存"}
                    </button>
                  </div>
                </>
              )}
              {status.credentialConfigured && (
                <>
                  <label className="text-[11px] font-medium text-slate-700">
                    当前仓库密码
                    <input
                      type="password"
                      autoComplete="current-password"
                      aria-label="当前仓库密码"
                      value={currentBackupPassword}
                      onChange={(event) =>
                        setCurrentBackupPassword(event.target.value)
                      }
                      className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[11px] outline-none focus:border-blue-400"
                    />
                  </label>
                  <label className="text-[11px] font-medium text-slate-700">
                    新仓库密码
                    <input
                      type="password"
                      autoComplete="new-password"
                      aria-label="新仓库密码"
                      value={newBackupPassword}
                      onChange={(event) =>
                        setNewBackupPassword(event.target.value)
                      }
                      className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[11px] outline-none focus:border-blue-400"
                    />
                  </label>
                  <label className="text-[11px] font-medium text-slate-700">
                    再次输入新仓库密码
                    <input
                      type="password"
                      autoComplete="new-password"
                      aria-label="再次输入新仓库密码"
                      value={newBackupPasswordConfirmation}
                      onChange={(event) =>
                        setNewBackupPasswordConfirmation(event.target.value)
                      }
                      className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[11px] outline-none focus:border-blue-400"
                    />
                  </label>
                  <div className="flex items-end">
                    <button
                      type="button"
                      disabled={
                        busy ||
                        loading ||
                        status.credentialRotationRecoveryPending
                      }
                      onClick={() => void previewRotation()}
                      className="h-9 rounded-lg border border-blue-300 bg-blue-50 px-3 text-[11px] font-semibold text-blue-700 disabled:opacity-40"
                    >
                      预览安全轮换
                    </button>
                  </div>
                </>
              )}
            </div>
            <NasBackupRemotePanel
              disabled={busy || status.credentialRotationRecoveryPending}
              onChanged={remoteChanged}
            />
          </>
        )}

        {status && (
          <div className="mt-3 flex flex-wrap gap-2 text-[10px]">
            <span className="rounded-full bg-slate-100 px-2.5 py-1 text-slate-600">
              调度器 {status.schedulerInstalled ? "已安装" : "缺失"}
            </span>
            <span className="rounded-full bg-slate-100 px-2.5 py-1 text-slate-600">
              加密凭据 {status.credentialConfigured ? "已配置" : "未配置"}
            </span>
            <span className="rounded-full bg-slate-100 px-2.5 py-1 text-slate-600">
              定时器 {status.timerEnabled ? "运行中" : "已停用"}
            </span>
          </div>
        )}

        {status?.credentialRotationRecoveryPending && (
          <p className="mt-3 flex items-start gap-2 rounded-xl bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-800">
            <ShieldAlertIcon className="mt-0.5 size-3.5 shrink-0" />
            上次仓库密码轮换仍在安全恢复中。请连接原外部备份盘并刷新；系统会根据持久化收据自动回滚未切换的新密钥，或完成旧密钥撤销。恢复完成前不会启动新的轮换或备份策略变更。
          </p>
        )}

        {status &&
          !ready &&
          !status.enabled &&
          !status.credentialRotationRecoveryPending && (
            <p className="mt-3 flex items-start gap-2 rounded-xl bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-800">
              <ShieldAlertIcon className="mt-0.5 size-3.5 shrink-0" />
              {!status.schedulerInstalled
                ? "本机尚未安装备份调度器，启用入口已锁定。"
                : "请先在上方初始化或连接 Restic 仓库。仓库密码仅随本次 HTTPS 请求进入内存，并以主机绑定的 systemd 凭据加密保存。"}
            </p>
          )}

        {recent.length > 0 && (
          <div className="mt-4 border-t border-slate-100 pt-3">
            <h3 className="text-[11px] font-semibold text-slate-700">
              最近运行
            </h3>
            <ul className="mt-2 space-y-1.5">
              {recent.map((attempt, index) => (
                <li
                  key={`${attempt.completedAt}-${index}`}
                  className="flex flex-wrap items-center gap-2 text-[10px] text-slate-500"
                >
                  <CheckCircle2Icon
                    className={`size-3.5 ${attempt.outcome === "failed" ? "text-red-500" : "text-emerald-500"}`}
                  />
                  <strong className="text-slate-700">
                    {outcomeLabel(attempt.outcome)}
                  </strong>
                  <time>
                    {new Date(attempt.completedAt).toLocaleString("zh-CN", {
                      hour12: false,
                    })}
                  </time>
                  {attempt.memberCount != null && (
                    <span>{attempt.memberCount} 个卷</span>
                  )}
                  {attempt.errorCode && <span>代码 {attempt.errorCode}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}

        <NasBackupRestorePanel
          status={status}
          parentBusy={busy || loading}
          repository={repository}
          repositoryMount={repositoryMount}
        />

        {error && (
          <p role="alert" className="mt-3 text-[11px] text-red-700">
            {error}
          </p>
        )}
      </section>
      <HighRiskApprovalDialog
        open={Boolean(pendingPlan)}
        title={
          pendingDesired?.enabled ? "确认启用每日备份" : "确认停用每日备份"
        }
        description="此操作会更新 root 管理的备份策略和 systemd 定时器。启用前会再次验证加密凭据、外部挂载盘和仓库边界；请输入管理员密码继续。"
        targetLabel="NAS 数据卷 · 加密 Restic 仓库"
        confirmLabel={pendingDesired?.enabled ? "确认启用" : "确认停用"}
        onCancel={() => {
          setPendingDesired(null);
          setPendingPlan(null);
        }}
        onConfirm={apply}
      />
      <HighRiskApprovalDialog
        open={Boolean(pendingCredentialPlan)}
        title={
          pendingCredentialDesired?.mode === "initialize"
            ? "确认初始化加密备份仓库"
            : "确认连接已有加密仓库"
        }
        description="此操作会验证外部挂载边界，并将仓库密码加密为仅本机 systemd 服务可解开的 root 凭据。密码不会写入日志、审计或浏览器存储；请输入设备管理员密码继续。"
        targetLabel="外部备份盘 · 主机绑定加密凭据"
        confirmLabel={
          pendingCredentialDesired?.mode === "initialize"
            ? "确认初始化"
            : "确认连接"
        }
        onCancel={() => {
          setPendingCredentialDesired(null);
          setPendingCredentialPlan(null);
          setBackupPassword("");
          setBackupPasswordConfirmation("");
        }}
        onConfirm={applyCredential}
      />
      <HighRiskApprovalDialog
        open={Boolean(pendingRotationPlan)}
        title="确认安全轮换仓库密码"
        description="此操作会先为 Restic 仓库添加并验证新密钥，再切换本机加密凭据，最后撤销所有仍可被旧密码解锁的密钥。密码不会写入日志、审计或浏览器存储；请输入设备管理员密码继续。"
        targetLabel="Restic 仓库·主机绑定加密凭据"
        confirmLabel="确认轮换"
        onCancel={clearRotationSecrets}
        onConfirm={applyRotation}
      />
    </>
  );
}
