import { useCallback, useEffect, useState } from "react";
import {
  CameraIcon,
  FolderSyncIcon,
  Loader2Icon,
  RefreshCwIcon,
  Trash2Icon,
} from "lucide-react";

import { requestHighRiskApproval } from "@/appliance/approval";
import {
  applyBtrfsSnapshot,
  applyBtrfsSnapshotDelete,
  applyBtrfsSnapshotRestoreCopy,
  applyBtrfsSnapshotSchedule,
  fetchBtrfsSnapshotSchedule,
  fetchBtrfsSnapshots,
  planBtrfsSnapshot,
  planBtrfsSnapshotDelete,
  planBtrfsSnapshotRestoreCopy,
  planBtrfsSnapshotSchedule,
  type BtrfsSnapshot,
  type BtrfsSnapshotDeletePlan,
  type BtrfsSnapshotDesired,
  type BtrfsSnapshotPlan,
  type BtrfsSnapshotRestoreCopyPlan,
  type BtrfsSnapshotSchedule,
  type BtrfsSnapshotSchedulePlan,
} from "@/appliance/btrfs-snapshots";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";

const SNAPSHOT_NAME = /^[a-z][a-z0-9_-]{0,31}$/;
const SHARE_NAME = /^(?=.{1,64}$)[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?$/;

type PendingApproval =
  | { kind: "create"; plan: BtrfsSnapshotPlan }
  | { kind: "delete"; plan: BtrfsSnapshotDeletePlan }
  | { kind: "restore"; plan: BtrfsSnapshotRestoreCopyPlan }
  | { kind: "schedule"; plan: BtrfsSnapshotSchedulePlan };

export function BtrfsSnapshotPanel({
  sharedFolderRef,
  sharedFolderName,
  canCreate,
  canDelete,
  canRestore,
  canSchedule,
  onRecovered,
}: {
  sharedFolderRef: string;
  sharedFolderName: string;
  canCreate: boolean;
  canDelete: boolean;
  canRestore: boolean;
  canSchedule: boolean;
  onRecovered?: () => void;
}) {
  const [snapshots, setSnapshots] = useState<BtrfsSnapshot[]>([]);
  const [name, setName] = useState("");
  const [plan, setPlan] = useState<BtrfsSnapshotPlan | null>(null);
  const [pending, setPending] = useState<PendingApproval | null>(null);
  const [loading, setLoading] = useState(true);
  const [planning, setPlanning] = useState(false);
  const [restoreSnapshot, setRestoreSnapshot] = useState<BtrfsSnapshot | null>(
    null,
  );
  const [restoreName, setRestoreName] = useState("");
  const [restorePlan, setRestorePlan] =
    useState<BtrfsSnapshotRestoreCopyPlan | null>(null);
  const [schedule, setSchedule] = useState<BtrfsSnapshotSchedule | null>(null);
  const [scheduleEnabled, setScheduleEnabled] = useState(false);
  const [keepLatest, setKeepLatest] = useState(8);
  const [schedulePlan, setSchedulePlan] =
    useState<BtrfsSnapshotSchedulePlan | null>(null);
  const [schedulePlanning, setSchedulePlanning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchBtrfsSnapshots(sharedFolderRef);
      setSnapshots(result.snapshots);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取快照");
    } finally {
      setLoading(false);
    }
  }, [sharedFolderRef]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const refreshSchedule = useCallback(async () => {
    if (!canSchedule) return;
    try {
      const result = await fetchBtrfsSnapshotSchedule(sharedFolderRef);
      setSchedule(result);
      setScheduleEnabled(result.enabled);
      setKeepLatest(result.keepLatest);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法读取自动快照策略",
      );
    }
  }, [canSchedule, sharedFolderRef]);

  useEffect(() => {
    void refreshSchedule();
  }, [refreshSchedule]);

  const previewCreate = async () => {
    const normalizedName = name.trim();
    if (
      !SNAPSHOT_NAME.test(normalizedName) ||
      normalizedName.startsWith("auto-")
    ) {
      setError(
        "名称须以小写字母开头，只能包含小写字母、数字、下划线或连字符；auto- 前缀由调度器保留",
      );
      return;
    }
    const desired: BtrfsSnapshotDesired = {
      schema: "echo.omv.btrfs-snapshot-desired.v1",
      sharedFolderRef,
      name: normalizedName,
    };
    setPlanning(true);
    setError(null);
    setPlan(null);
    try {
      setPlan(await planBtrfsSnapshot(desired));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法生成快照预览");
    } finally {
      setPlanning(false);
    }
  };

  const previewDelete = async (snapshot: BtrfsSnapshot) => {
    setPlanning(true);
    setError(null);
    try {
      const deletePlan = await planBtrfsSnapshotDelete({
        schema: "echo.omv.btrfs-snapshot-delete-desired.v1",
        sharedFolderRef,
        snapshotId: snapshot.snapshotId,
      });
      setPending({ kind: "delete", plan: deletePlan });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法生成删除预览");
    } finally {
      setPlanning(false);
    }
  };

  const beginRestore = (snapshot: BtrfsSnapshot) => {
    const base = sharedFolderName.slice(0, 54).replace(/[.-]+$/, "") || "share";
    setRestoreSnapshot(snapshot);
    setRestoreName(`${base}_recovered`);
    setRestorePlan(null);
    setError(null);
  };

  const previewRestore = async () => {
    const normalizedName = restoreName.trim();
    if (!restoreSnapshot || !SHARE_NAME.test(normalizedName)) {
      setError(
        "恢复副本名称须为 1–64 位可跨 Windows/macOS/Linux 使用的单段名称",
      );
      return;
    }
    setPlanning(true);
    setRestorePlan(null);
    setError(null);
    try {
      setRestorePlan(
        await planBtrfsSnapshotRestoreCopy({
          schema: "echo.omv.btrfs-snapshot-restore-copy-desired.v1",
          sharedFolderRef,
          snapshotId: restoreSnapshot.snapshotId,
          name: normalizedName,
        }),
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法生成恢复副本预览",
      );
    } finally {
      setPlanning(false);
    }
  };

  const previewSchedule = async () => {
    if (!Number.isInteger(keepLatest) || keepLatest < 1 || keepLatest > 64) {
      setError("自动快照保留数量必须在 1 到 64 之间");
      return;
    }
    setSchedulePlanning(true);
    setSchedulePlan(null);
    setError(null);
    try {
      setSchedulePlan(
        await planBtrfsSnapshotSchedule({
          schema: "echo.btrfs-snapshot-schedule-desired.v1",
          sharedFolderRef,
          enabled: scheduleEnabled,
          keepLatest,
        }),
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法生成自动快照策略预览",
      );
    } finally {
      setSchedulePlanning(false);
    }
  };

  const confirm = async (password: string) => {
    if (!pending) return;
    if (pending.kind === "create") {
      const approval = await requestHighRiskApproval(
        "omv.btrfs-snapshot.create",
        pending.plan.planId,
        password,
      );
      await applyBtrfsSnapshot(
        pending.plan.desired,
        pending.plan.planId,
        approval.approvalToken,
      );
      setName("");
      setPlan(null);
    } else if (pending.kind === "delete") {
      const approval = await requestHighRiskApproval(
        "omv.btrfs-snapshot.delete",
        pending.plan.planId,
        password,
      );
      await applyBtrfsSnapshotDelete(
        pending.plan.desired,
        pending.plan.planId,
        approval.approvalToken,
      );
    } else if (pending.kind === "restore") {
      const approval = await requestHighRiskApproval(
        "omv.btrfs-snapshot.restore-copy",
        pending.plan.planId,
        password,
      );
      await applyBtrfsSnapshotRestoreCopy(
        pending.plan.desired,
        pending.plan.planId,
        approval.approvalToken,
      );
      setRestoreSnapshot(null);
      setRestoreName("");
      setRestorePlan(null);
      onRecovered?.();
    } else {
      const approval = await requestHighRiskApproval(
        "storage.btrfs.snapshot.schedule",
        pending.plan.planId,
        password,
      );
      await applyBtrfsSnapshotSchedule(
        pending.plan.desired,
        pending.plan.planId,
        approval.approvalToken,
      );
      setSchedulePlan(null);
    }
    setPending(null);
    await Promise.all([refresh(), refreshSchedule()]);
  };

  return (
    <div className="mt-3 rounded-lg border border-violet-200 bg-violet-50/70 p-3 text-[10px] text-violet-950">
      <div className="flex flex-wrap items-center gap-2">
        <CameraIcon className="size-3.5 text-violet-600" />
        <strong className="text-[11px]">只读文件快照</strong>
        <span className="text-violet-600">
          {loading ? "正在读取…" : `${snapshots.length}/256`}
        </span>
        <button
          type="button"
          aria-label={`刷新 ${sharedFolderName} 的快照`}
          onClick={() => void refresh()}
          disabled={loading}
          className="ml-auto rounded p-1 text-violet-600 hover:bg-violet-100 disabled:opacity-50"
        >
          <RefreshCwIcon
            className={`size-3 ${loading ? "animate-spin" : ""}`}
          />
        </button>
      </div>
      <p className="mt-1 leading-4 text-violet-700">
        同卷、只读、崩溃一致；不会暂停应用，也暂不提供整卷恢复。
      </p>
      {canSchedule && schedule && (
        <div className="mt-2 rounded-md border border-violet-200 bg-white/75 p-2">
          <div className="flex flex-wrap items-center gap-2">
            <label className="inline-flex items-center gap-1.5 font-medium">
              <input
                type="checkbox"
                checked={scheduleEnabled}
                onChange={(event) => {
                  setScheduleEnabled(event.currentTarget.checked);
                  setSchedulePlan(null);
                }}
              />
              每日自动快照
            </label>
            <label className="ml-auto inline-flex items-center gap-1.5">
              仅保留最新
              <input
                aria-label={`${sharedFolderName} 自动快照保留数量`}
                type="number"
                min={1}
                max={64}
                value={keepLatest}
                disabled={!scheduleEnabled}
                onChange={(event) => {
                  setKeepLatest(event.currentTarget.valueAsNumber);
                  setSchedulePlan(null);
                }}
                className="h-7 w-14 rounded border border-violet-200 bg-white px-1.5 text-center outline-none focus:border-violet-500 disabled:opacity-50"
              />
              个
            </label>
            <button
              type="button"
              onClick={() => void previewSchedule()}
              disabled={schedulePlanning}
              className="h-7 rounded-lg border border-violet-300 bg-white px-2.5 font-medium text-violet-700 hover:bg-violet-100 disabled:opacity-50"
            >
              {schedulePlanning ? "正在预览…" : "预览策略"}
            </button>
          </div>
          <p className="mt-1 text-violet-600">
            每日 02:15
            后随机错峰；只自动清理调度器创建的快照，手工快照不受影响。
          </p>
          {schedulePlan && (
            <div className="mt-2 flex items-center justify-between gap-2 border-t border-violet-100 pt-2">
              <span>
                {schedulePlan.operation === "none"
                  ? "策略没有变化。"
                  : schedulePlan.operation === "disable"
                    ? "将停用自动创建；已有快照不会立即删除。"
                    : `将启用每日自动快照，并仅保留最新 ${schedulePlan.desired.keepLatest} 个自动快照。`}
              </span>
              {schedulePlan.requiresApproval && (
                <button
                  type="button"
                  onClick={() =>
                    setPending({ kind: "schedule", plan: schedulePlan })
                  }
                  className="h-7 shrink-0 rounded-lg bg-amber-500 px-2.5 font-medium text-white hover:bg-amber-600"
                >
                  管理员确认
                </button>
              )}
            </div>
          )}
        </div>
      )}
      {canCreate && (
        <div className="mt-2 flex flex-wrap gap-2">
          <input
            aria-label={`${sharedFolderName} 的快照名称`}
            value={name}
            maxLength={32}
            autoCapitalize="none"
            autoComplete="off"
            spellCheck={false}
            placeholder="例如 before_upgrade"
            onChange={(event) => {
              setName(event.currentTarget.value);
              setPlan(null);
            }}
            className="h-8 min-w-[190px] flex-1 rounded-lg border border-violet-200 bg-white px-2.5 text-[11px] outline-none focus:border-violet-500"
          />
          <button
            type="button"
            onClick={() => void previewCreate()}
            disabled={planning || !name.trim() || snapshots.length >= 256}
            className="inline-flex h-8 items-center gap-1 rounded-lg bg-violet-600 px-3 font-medium text-white hover:bg-violet-700 disabled:opacity-50"
          >
            {planning && <Loader2Icon className="size-3 animate-spin" />}
            预览创建
          </button>
        </div>
      )}
      {plan && (
        <div className="mt-2 flex items-center justify-between gap-2 border-t border-violet-200 pt-2">
          <span>
            {plan.operation === "none"
              ? "同名快照已经存在，不会覆盖。"
              : `将创建只读快照 ${plan.desired.name}。`}
          </span>
          {plan.requiresApproval && (
            <button
              type="button"
              onClick={() => setPending({ kind: "create", plan })}
              className="h-7 shrink-0 rounded-lg bg-amber-500 px-2.5 font-medium text-white hover:bg-amber-600"
            >
              管理员确认
            </button>
          )}
        </div>
      )}
      {canRestore && restoreSnapshot && (
        <div className="mt-2 rounded-md border border-emerald-200 bg-emerald-50/80 p-2 text-emerald-950">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">
              从 {restoreSnapshot.name} 创建可写恢复副本
            </span>
            <input
              aria-label={`${restoreSnapshot.name} 的恢复副本名称`}
              value={restoreName}
              maxLength={64}
              autoCapitalize="none"
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => {
                setRestoreName(event.currentTarget.value);
                setRestorePlan(null);
              }}
              className="h-8 min-w-[190px] flex-1 rounded-lg border border-emerald-200 bg-white px-2.5 text-[11px] outline-none focus:border-emerald-500"
            />
            <button
              type="button"
              onClick={() => void previewRestore()}
              disabled={planning || !restoreName.trim()}
              className="h-8 rounded-lg bg-emerald-600 px-3 font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              预览恢复
            </button>
            <button
              type="button"
              onClick={() => {
                setRestoreSnapshot(null);
                setRestorePlan(null);
              }}
              className="h-8 rounded-lg border border-emerald-200 bg-white px-3 text-emerald-700 hover:bg-emerald-100"
            >
              取消
            </button>
          </div>
          <p className="mt-1 text-emerald-700">
            新目录与原共享位于同一 Btrfs 卷；不会覆盖原共享，也不会自动启用
            SMB/NFS。
          </p>
          {restorePlan && (
            <div className="mt-2 flex items-center justify-between gap-2 border-t border-emerald-200 pt-2">
              <span>
                将创建可写共享目录 {restorePlan.desired.name}
                ；源共享和只读快照保持不变。
              </span>
              <button
                type="button"
                onClick={() =>
                  setPending({ kind: "restore", plan: restorePlan })
                }
                className="h-7 shrink-0 rounded-lg bg-amber-500 px-2.5 font-medium text-white hover:bg-amber-600"
              >
                管理员确认
              </button>
            </div>
          )}
        </div>
      )}
      {!loading && snapshots.length > 0 && (
        <ul className="mt-2 space-y-1 border-t border-violet-200 pt-2">
          {snapshots.map((snapshot) => (
            <li
              key={snapshot.snapshotId}
              className="flex items-center gap-2 rounded-md bg-white px-2 py-1.5 ring-1 ring-violet-100"
            >
              <span className="min-w-0 flex-1 truncate font-medium">
                {snapshot.name}
              </span>
              <span className="text-violet-500">
                {snapshot.kind === "automatic" ? "自动 · 只读" : "手工 · 只读"}
              </span>
              {canRestore && (
                <button
                  type="button"
                  aria-label={`从快照 ${snapshot.name} 创建恢复副本`}
                  disabled={planning}
                  onClick={() => beginRestore(snapshot)}
                  className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-emerald-700 hover:bg-emerald-50 disabled:opacity-50"
                >
                  <FolderSyncIcon className="size-3" />
                  恢复副本
                </button>
              )}
              {canDelete && (
                <button
                  type="button"
                  aria-label={`删除快照 ${snapshot.name}`}
                  disabled={planning}
                  onClick={() => void previewDelete(snapshot)}
                  className="rounded p-1 text-red-600 hover:bg-red-50 disabled:opacity-50"
                >
                  <Trash2Icon className="size-3" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {error && (
        <p
          role="alert"
          className="mt-2 rounded-md bg-red-50 px-2 py-1.5 text-red-700"
        >
          {error}
        </p>
      )}
      <HighRiskApprovalDialog
        open={Boolean(pending)}
        title={
          pending?.kind === "delete"
            ? "删除只读快照"
            : pending?.kind === "restore"
              ? "创建恢复副本"
              : pending?.kind === "schedule"
                ? "更新自动快照策略"
                : "创建只读快照"
        }
        description={
          pending?.kind === "delete"
            ? "只删除所选快照子卷，不触碰共享文件夹源数据。删除后不能通过 Echo 恢复。"
            : pending?.kind === "restore"
              ? "从只读快照创建新的可写 Btrfs 共享目录；不会覆盖原共享，也不会自动发布 SMB/NFS。"
              : pending?.kind === "schedule"
                ? "后续计划任务将无需再次输入密码创建自动快照，并只裁剪由调度器创建的旧快照。"
                : "创建同一 Btrfs 文件系统内的只读、崩溃一致快照；不会暂停正在写入的应用。"
        }
        targetLabel={
          pending?.kind === "delete"
            ? pending.plan.snapshot.name
            : pending?.kind === "restore"
              ? `${pending.plan.sourceSnapshot.name} → ${pending.plan.desired.name}`
              : pending?.kind === "schedule"
                ? `${sharedFolderName} · 保留 ${pending.plan.desired.keepLatest} 个`
                : pending?.plan.desired.name
        }
        confirmLabel={
          pending?.kind === "delete"
            ? "确认删除"
            : pending?.kind === "restore"
              ? "确认创建副本"
              : pending?.kind === "schedule"
                ? "确认更新"
                : "确认创建"
        }
        destructive={pending?.kind === "delete"}
        onCancel={() => setPending(null)}
        onConfirm={confirm}
      />
    </div>
  );
}
