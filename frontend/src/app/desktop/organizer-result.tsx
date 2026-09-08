import { useEffect, useRef, useState } from "react";

import type { DesktopMoveOutcome, DesktopMoveResult } from "@/types/electron";

export type OrganizerMode = "move" | "undo";
export interface OrganizerReceipt {
  mode: OrganizerMode;
  result: DesktopMoveResult;
  completed: number;
  skipped: number;
  failed: number;
  conflicts: number;
  uncertain: number;
  incomplete: boolean;
}

const count = (value: unknown): number =>
  typeof value === "number" && Number.isInteger(value) && value >= 0
    ? value
    : 0;

export function organizerReceipt(
  mode: OrganizerMode,
  result: DesktopMoveResult,
  single = false,
): OrganizerReceipt {
  const outcomes = result.outcomes ?? [];
  const completed = Math.max(
    count(mode === "undo" ? result.undone : result.moved),
    outcomes.filter(
      (row) => row.status === (mode === "undo" ? "undone" : "moved"),
    ).length,
    single && result.ok && !result.skipped && !outcomes.length ? 1 : 0,
  );
  const skipped = Math.max(
    count(result.skipped),
    result.skipped === true ? 1 : 0,
    outcomes.filter((row) => row.status === "skipped").length,
  );
  const conflicts = Math.max(
    count(result.conflicts),
    outcomes.filter((row) => row.status === "conflict").length,
  );
  const uncertain = Math.max(
    count(result.uncertain),
    outcomes.filter((row) => row.status === "uncertain").length,
  );
  const failed = Math.max(
    count(result.failed),
    outcomes.filter((row) => row.status === "failed").length,
    !result.ok && !conflicts && !uncertain ? 1 : 0,
  );
  return {
    mode,
    result,
    completed,
    skipped,
    failed,
    conflicts,
    uncertain,
    incomplete:
      !result.ok || Boolean(skipped || failed || conflicts || uncertain),
  };
}

export function useDesktopOrganizer(refresh: () => void) {
  const [busy, setBusy] = useState<OrganizerMode | null>(null);
  const [receipt, setReceipt] = useState<OrganizerReceipt | null>(null);
  const busyRef = useRef(false);
  const alive = useRef(true);
  const retryId = useRef<string | undefined>(undefined);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const run = async (
    mode: OrganizerMode,
    action: (operationId?: string) => Promise<DesktopMoveResult>,
    single = false,
  ) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(mode);
    let next: OrganizerReceipt;
    try {
      next = organizerReceipt(
        mode,
        await action(mode === "undo" ? retryId.current : undefined),
        single,
      );
    } catch {
      // A lost IPC response does not prove that no files moved.
      next = organizerReceipt(mode, {
        ok: false,
        uncertain: 1,
        error: "未收到完整结果，请检查刷新后的文件列表，再决定是否撤销。",
      });
    } finally {
      busyRef.current = false;
    }
    if (!alive.current) return;
    if (next.result.operationId) retryId.current = next.result.operationId;
    if (mode === "undo" && !next.incomplete) retryId.current = undefined;
    setReceipt(next);
    setBusy(null);
    refresh();
  };
  return { busy, receipt, run };
}

function outcomeMessage(row: DesktopMoveOutcome): string {
  const reasons: Record<string, string> = {
    destination_exists: "目标已有同名文件，未覆盖",
    target_exists: "目标已有同名文件，未覆盖",
    source_changed: "文件内容已变化，未撤销",
    file_changed: "文件内容已变化，未撤销",
    source_conflict: "原位置已有文件，未覆盖；请检查后重试撤销",
    destination_changed: "目标文件已变化，未撤销",
    newer_operation_pending: "请先撤销同一文件的后续操作",
    content_changed: "文件内容已变化，未撤销",
    legacy_unverifiable: "旧记录无法验证内容，保留文件供检查",
    source_replaced:
      "发现文件被替换，已保留恢复副本；请检查列出的路径，勿重复整理",
    recovery_required: "已保留恢复副本；请检查列出的路径，勿重复整理",
  };
  const reason = row.code ? reasons[row.code] : undefined;
  if (reason) return reason;
  return {
    moved: "已移动",
    undone: "已撤销",
    skipped: "已跳过，文件保留",
    conflict: "存在冲突，文件保留，请检查后重试撤销",
    failed: "操作失败，请检查文件",
    uncertain: "结果待确认，请检查文件及恢复副本",
  }[row.status];
}

export function OrganizerResult({ receipt }: { receipt: OrganizerReceipt }) {
  const {
    mode,
    result,
    completed,
    skipped,
    conflicts,
    failed,
    uncertain,
    incomplete,
  } = receipt;
  return (
    <section
      aria-label="桌面整理结果"
      role="status"
      className={`mt-3 rounded-xl border p-3 text-xs ${incomplete ? "border-amber-200 bg-amber-50 text-amber-900" : "border-emerald-200 bg-emerald-50 text-emerald-900"}`}
    >
      <p className="font-medium">
        {uncertain
          ? "部分结果待确认"
          : incomplete
            ? "操作尚未全部完成"
            : completed
              ? "操作完成"
              : "没有执行文件移动"}
      </p>
      <p className="mt-1">
        {mode === "undo" ? "已撤销" : "已移动"} {completed} 项
        {skipped > 0 && ` · 跳过 ${skipped} 项`}
        {conflicts > 0 && ` · 冲突 ${conflicts} 项`}
        {failed > 0 && ` · 失败 ${failed} 项`}
        {uncertain > 0 && ` · 待确认 ${uncertain} 项`}
      </p>
      {mode === "undo" && incomplete && (
        <p className="mt-1">
          {result.operationId
            ? "未完成的撤销记录已保留，检查文件后可再次点击撤销。"
            : "请先核对文件与上次操作结果，再决定是否继续撤销。"}
        </p>
      )}
      {result.error && <p className="mt-1">{result.error}</p>}
      {!!result.outcomes?.length && (
        <details className="mt-2" open={incomplete}>
          <summary className="cursor-pointer">查看逐项结果</summary>
          <ul className="mt-2 max-h-40 space-y-2 overflow-auto">
            {result.outcomes.map((row, index) => (
              <li key={`${row.srcPath}:${index}`}>
                <p className="break-all">{row.srcPath}</p>
                <p>{outcomeMessage(row)}</p>
                {row.destPath && (
                  <p className="break-all">目标：{row.destPath}</p>
                )}
                {row.recoveryPaths?.map((path) => (
                  <p key={path} className="break-all">
                    恢复副本：{path}
                  </p>
                ))}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
