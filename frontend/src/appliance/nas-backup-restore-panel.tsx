import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle2Icon,
  DatabaseBackupIcon,
  Loader2Icon,
  ShieldAlertIcon,
} from "lucide-react";

import { requestHighRiskApproval } from "@/appliance/approval";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";
import {
  applyNasBackupRestore,
  fetchNasBackupRestoreSets,
  fetchNasBackupRestoreTargets,
  planNasBackupRestore,
  type NasBackupRestoreDesired,
  type NasBackupRestorePlan,
  type NasBackupRestoreResult,
  type NasBackupRestoreSetList,
  type NasBackupRestoreTargetList,
  type NasBackupScheduleStatus,
} from "@/appliance/nas-backup";

type Props = {
  status: NasBackupScheduleStatus | null;
  parentBusy?: boolean;
  repository: string;
  repositoryMount: string;
};

function shortId(value: string) {
  return `${value.slice(0, 8)}…${value.slice(-6)}`;
}

function defaultTargetBindings(
  members: Array<{ sharedFolderRef: string }>,
  targets: NasBackupRestoreTargetList["targets"],
) {
  const available = [...targets].sort(
    (left, right) => Number(right.empty) - Number(left.empty),
  );
  const used = new Set<string>();
  return Object.fromEntries(
    members.map((member) => {
      const target =
        available.find(
          (item) =>
            item.sharedFolderRef === member.sharedFolderRef &&
            !used.has(item.sharedFolderRef),
        ) ?? available.find((item) => !used.has(item.sharedFolderRef));
      if (target) used.add(target.sharedFolderRef);
      return [member.sharedFolderRef, target?.sharedFolderRef ?? ""];
    }),
  );
}

export function NasBackupRestorePanel({
  status,
  parentBusy = false,
  repository,
  repositoryMount,
}: Props) {
  const [listing, setListing] = useState<NasBackupRestoreSetList | null>(null);
  const [targetListing, setTargetListing] =
    useState<NasBackupRestoreTargetList | null>(null);
  const [targetBindings, setTargetBindings] = useState<Record<string, string>>(
    {},
  );
  const [selector, setSelector] = useState("");
  const [desired, setDesired] = useState<NasBackupRestoreDesired | null>(null);
  const [plan, setPlan] = useState<NasBackupRestorePlan | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [result, setResult] = useState<NasBackupRestoreResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const available = Boolean(
    status?.credentialConfigured &&
    !status.enabled &&
    !status.timerEnabled &&
    !status.credentialRotationRecoveryPending &&
    repository.trim().startsWith("/") &&
    repositoryMount.trim().startsWith("/"),
  );
  const selected = useMemo(
    () => listing?.sets.find((item) => item.setId === selector) ?? null,
    [listing, selector],
  );
  const mappingsReady = Boolean(
    selected &&
    selected.members.length > 0 &&
    selected.members.every(
      (member) => targetBindings[member.sharedFolderRef],
    ) &&
    new Set(
      selected.members.map((member) => targetBindings[member.sharedFolderRef]),
    ).size === selected.members.length,
  );

  useEffect(() => {
    setListing(null);
    setTargetListing(null);
    setTargetBindings({});
    setSelector("");
    setDesired(null);
    setPlan(null);
    setConfirmation("");
    setApprovalOpen(false);
  }, [
    repository,
    repositoryMount,
    status?.credentialConfigured,
    status?.credentialRotationRecoveryPending,
    status?.enabled,
    status?.timerEnabled,
  ]);

  const loadSets = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const [value, targetValue] = await Promise.all([
        fetchNasBackupRestoreSets({
          schema: "echo.nas-data-backup-restore-repository.v1",
          repository: repository.trim(),
          repositoryMount: repositoryMount.trim(),
        }),
        fetchNasBackupRestoreTargets(),
      ]);
      const nextSelector = value.sets.some((item) => item.setId === selector)
        ? selector
        : (value.sets[0]?.setId ?? "");
      const nextSet = value.sets.find((item) => item.setId === nextSelector);
      setListing(value);
      setTargetListing(targetValue);
      setSelector(nextSelector);
      setTargetBindings(
        defaultTargetBindings(nextSet?.members ?? [], targetValue.targets),
      );
    } catch (reason) {
      setListing(null);
      setTargetListing(null);
      setTargetBindings({});
      setError(
        reason instanceof Error ? reason.message : "无法读取 NAS 可恢复版本",
      );
    } finally {
      setBusy(false);
    }
  };

  const preview = async () => {
    if (!selected || !mappingsReady) return;
    const nextDesired: NasBackupRestoreDesired = {
      schema: "echo.nas-data-backup-restore-desired.v2",
      selector,
      repository: repository.trim(),
      repositoryMount: repositoryMount.trim(),
      targets: selected.members.map((member) => ({
        sourceSharedFolderRef: member.sharedFolderRef,
        // `mappingsReady` above proves every selected member has a target;
        // the non-null assertion preserves that invariant for indexed access.
        targetSharedFolderRef: targetBindings[member.sharedFolderRef]!,
      })),
    };
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const nextPlan = await planNasBackupRestore(nextDesired);
      setDesired(nextDesired);
      setPlan(nextPlan);
      setConfirmation("");
    } catch (reason) {
      setDesired(null);
      setPlan(null);
      setError(
        reason instanceof Error ? reason.message : "无法生成 NAS 数据恢复预览",
      );
    } finally {
      setBusy(false);
    }
  };

  const apply = async (administratorPassword: string) => {
    if (!desired || !plan || confirmation !== plan.confirmation) return;
    setBusy(true);
    setError(null);
    try {
      const approval = await requestHighRiskApproval(
        "storage.nas-backup.restore",
        plan.planId,
        administratorPassword,
      );
      const restored = await applyNasBackupRestore(
        desired,
        plan.planId,
        confirmation,
        approval.approvalToken,
      );
      setResult(restored);
      setPlan(null);
      setDesired(null);
      setConfirmation("");
      setApprovalOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法恢复 NAS 数据");
      throw reason;
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="mt-5 border-t border-slate-100 pt-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="flex items-center gap-2 text-[12px] font-semibold text-slate-800">
              <DatabaseBackupIcon className="size-4 text-violet-600" />
              从加密备份恢复数据
            </h3>
            <p className="mt-1 max-w-2xl text-[10px] leading-5 text-slate-500">
              每个备份来源都要明确映射到当前机器的受管 Btrfs
              共享卷，可选择新硬盘。
              新恢复要求目标为空；续跑会识别持久化收据。恢复前必须停用备份、自动
              快照并解除 SMB、NFS 与 Time Machine 发布。
            </p>
          </div>
          <button
            type="button"
            disabled={!available || busy || parentBusy}
            onClick={() => void loadSets()}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-violet-300 bg-violet-50 px-3 text-[11px] font-semibold text-violet-700 disabled:opacity-40"
          >
            {busy && <Loader2Icon className="size-3 animate-spin" />}
            读取可恢复版本
          </button>
        </div>

        {!available && status && (
          <p className="mt-3 flex items-start gap-2 rounded-xl bg-amber-50 px-3 py-2 text-[10px] leading-5 text-amber-800">
            <ShieldAlertIcon className="mt-0.5 size-3.5 shrink-0" />
            {!status.credentialConfigured
              ? "请先连接加密 Restic 仓库。"
              : status.credentialRotationRecoveryPending
                ? "先完成仓库凭据轮换恢复。"
                : status.enabled || status.timerEnabled
                  ? "先停用每日备份及其定时器，恢复入口才会解锁。"
                  : "请在上方重新填写外部备份盘挂载点和 Restic 仓库目录。"}
          </p>
        )}

        {listing && (
          <div className="mt-3 rounded-xl bg-slate-50 p-3">
            <div className="flex flex-wrap items-end gap-3">
              <label className="min-w-0 flex-1 text-[10px] font-medium text-slate-700">
                已认证的备份集
                <select
                  aria-label="已认证的 NAS 备份集"
                  value={selector}
                  onChange={(event) => {
                    const nextSelector = event.target.value;
                    const nextSet = listing.sets.find(
                      (item) => item.setId === nextSelector,
                    );
                    setSelector(nextSelector);
                    setTargetBindings(
                      defaultTargetBindings(
                        nextSet?.members ?? [],
                        targetListing?.targets ?? [],
                      ),
                    );
                    setPlan(null);
                    setDesired(null);
                    setConfirmation("");
                    setResult(null);
                  }}
                  className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[11px] outline-none focus:border-violet-400"
                >
                  {listing.sets.map((item) => (
                    <option key={item.setId} value={item.setId}>
                      {new Date(item.createdAt).toLocaleString("zh-CN", {
                        hour12: false,
                      })}
                      {` · ${item.memberCount} 个卷 · ${shortId(item.setId)}`}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                disabled={!mappingsReady || busy || parentBusy}
                onClick={() => void preview()}
                className="h-9 rounded-lg bg-violet-600 px-3 text-[11px] font-semibold text-white disabled:opacity-40"
              >
                检查恢复条件
              </button>
            </div>
            {selected && selected.members.length > 0 && (
              <div className="mt-3 space-y-2">
                {selected.members.map((member) => (
                  <label
                    key={member.sharedFolderRef}
                    className="grid gap-1 text-[10px] font-medium text-slate-700 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] sm:items-center"
                  >
                    <span>来源 {shortId(member.sharedFolderRef)}</span>
                    <select
                      aria-label={`恢复目标 ${shortId(member.sharedFolderRef)}`}
                      value={targetBindings[member.sharedFolderRef] ?? ""}
                      onChange={(event) => {
                        setTargetBindings((current) => ({
                          ...current,
                          [member.sharedFolderRef]: event.target.value,
                        }));
                        setPlan(null);
                        setDesired(null);
                        setConfirmation("");
                        setResult(null);
                      }}
                      className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[11px] outline-none focus:border-violet-400"
                    >
                      <option value="">选择目标共享卷</option>
                      {targetListing?.targets.map((target) => {
                        const usedByAnother = selected.members.some(
                          (other) =>
                            other.sharedFolderRef !== member.sharedFolderRef &&
                            targetBindings[other.sharedFolderRef] ===
                              target.sharedFolderRef,
                        );
                        return (
                          <option
                            key={target.sharedFolderRef}
                            value={target.sharedFolderRef}
                            disabled={usedByAnother}
                          >
                            {target.name} · {shortId(target.filesystemUuid)}
                            {target.empty ? " · 空" : " · 非空（仅限续跑）"}
                          </option>
                        );
                      })}
                    </select>
                  </label>
                ))}
                {targetListing?.targets.length === 0 && (
                  <p className="text-[10px] text-amber-700">
                    没有可用的未发布受管 Btrfs
                    共享卷，请先创建目标并停用相关服务。
                  </p>
                )}
              </div>
            )}
            {listing.sets.length === 0 && (
              <p className="mt-2 text-[10px] text-slate-500">
                仓库中没有可认证的多卷 NAS 备份集。
              </p>
            )}
            {listing.truncated && (
              <p className="mt-2 text-[10px] text-amber-700">
                当前仅显示最近 50 个备份集。
              </p>
            )}
          </div>
        )}

        {plan && (
          <div className="mt-3 rounded-xl border border-violet-200 bg-violet-50/70 p-3">
            <p className="text-[10px] leading-5 text-violet-900">
              {plan.operation === "resumeRestore"
                ? "检测到未完成的持久化恢复事务，将从安全收据继续。"
                : plan.operation === "verifyRestore"
                  ? "该备份集已经恢复；本次只会重新验证现有内容。"
                  : `恢复 ${plan.memberCount} 个空共享卷；完整读取仓库后逐卷原子晋级。`}
            </p>
            <code className="mt-2 block break-all rounded-lg bg-white px-2.5 py-2 text-[10px] text-slate-700 ring-1 ring-violet-100">
              {plan.confirmation}
            </code>
            <label className="mt-3 block text-[10px] font-medium text-slate-700">
              输入上面的完整确认句
              <input
                aria-label="NAS 恢复确认句"
                autoComplete="off"
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
                className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 font-mono text-[10px] outline-none focus:border-violet-400"
              />
            </label>
            <div className="mt-3 flex justify-end">
              <button
                type="button"
                disabled={
                  busy || parentBusy || confirmation !== plan.confirmation
                }
                onClick={() => setApprovalOpen(true)}
                className="h-8 rounded-lg bg-red-600 px-3 text-[11px] font-semibold text-white disabled:opacity-40"
              >
                管理员审批并恢复
              </button>
            </div>
          </div>
        )}

        {result && (
          <p className="mt-3 flex items-start gap-2 rounded-xl bg-emerald-50 px-3 py-2 text-[10px] leading-5 text-emerald-800">
            <CheckCircle2Icon className="mt-0.5 size-3.5 shrink-0" />
            已完成 {result.memberCount}{" "}
            个卷的恢复并逐卷校验内容，仓库完整读取校验通过。
          </p>
        )}

        {error && (
          <p role="alert" className="mt-3 text-[10px] text-red-700">
            {error}
          </p>
        )}
      </div>

      <HighRiskApprovalDialog
        open={approvalOpen && Boolean(plan)}
        title={
          plan?.recoveryPending ? "继续 NAS 数据恢复" : "确认恢复 NAS 数据"
        }
        description="恢复计划已绑定加密仓库、精确备份集，以及每个来源到目标 Btrfs 共享卷的映射。服务端会再次检查目标身份与未发布状态、完整读取仓库，并使用持久化收据处理掉电续跑；请输入设备管理员密码继续。"
        targetLabel={
          plan
            ? `${plan.memberCount} 个 NAS 数据卷 · ${shortId(plan.setId)}`
            : "NAS 数据卷"
        }
        confirmLabel={plan?.recoveryPending ? "确认继续恢复" : "确认恢复"}
        onCancel={() => setApprovalOpen(false)}
        onConfirm={apply}
      />
    </>
  );
}
