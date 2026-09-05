import { useCallback, useEffect, useState } from "react";
import {
  CameraIcon,
  Loader2Icon,
  RefreshCwIcon,
  Trash2Icon,
} from "lucide-react";

import { requestHighRiskApproval } from "@/appliance/approval";
import {
  applyBtrfsSnapshot,
  applyBtrfsSnapshotDelete,
  fetchBtrfsSnapshots,
  planBtrfsSnapshot,
  planBtrfsSnapshotDelete,
  type BtrfsSnapshot,
  type BtrfsSnapshotDeletePlan,
  type BtrfsSnapshotDesired,
  type BtrfsSnapshotPlan,
} from "@/appliance/btrfs-snapshots";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";

const SNAPSHOT_NAME = /^[a-z][a-z0-9_-]{0,31}$/;

type PendingApproval =
  | { kind: "create"; plan: BtrfsSnapshotPlan }
  | { kind: "delete"; plan: BtrfsSnapshotDeletePlan };

export function BtrfsSnapshotPanel({
  sharedFolderRef,
  sharedFolderName,
  canCreate,
  canDelete,
}: {
  sharedFolderRef: string;
  sharedFolderName: string;
  canCreate: boolean;
  canDelete: boolean;
}) {
  const [snapshots, setSnapshots] = useState<BtrfsSnapshot[]>([]);
  const [name, setName] = useState("");
  const [plan, setPlan] = useState<BtrfsSnapshotPlan | null>(null);
  const [pending, setPending] = useState<PendingApproval | null>(null);
  const [loading, setLoading] = useState(true);
  const [planning, setPlanning] = useState(false);
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

  const previewCreate = async () => {
    const normalizedName = name.trim();
    if (!SNAPSHOT_NAME.test(normalizedName)) {
      setError("名称须以小写字母开头，只能包含小写字母、数字、下划线或连字符");
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
    } else {
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
    }
    setPending(null);
    await refresh();
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
        同卷、只读、崩溃一致；不会暂停应用，也暂不提供整卷恢复或自动保留策略。
      </p>
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
              <span className="text-violet-500">只读</span>
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
        title={pending?.kind === "delete" ? "删除只读快照" : "创建只读快照"}
        description={
          pending?.kind === "delete"
            ? "只删除所选快照子卷，不触碰共享文件夹源数据。删除后不能通过 Echo 恢复。"
            : "创建同一 Btrfs 文件系统内的只读、崩溃一致快照；不会暂停正在写入的应用。"
        }
        targetLabel={
          pending?.kind === "delete"
            ? pending.plan.snapshot.name
            : pending?.plan.desired.name
        }
        confirmLabel={pending?.kind === "delete" ? "确认删除" : "确认创建"}
        destructive={pending?.kind === "delete"}
        onCancel={() => setPending(null)}
        onConfirm={confirm}
      />
    </div>
  );
}
