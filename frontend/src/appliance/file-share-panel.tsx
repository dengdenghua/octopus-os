import {
  CopyIcon,
  Link2Icon,
  Loader2Icon,
  ShieldOffIcon,
  XIcon,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { requestHighRiskApproval } from "@/appliance/approval";
import {
  applyFileShare,
  listFileShares,
  planFileShare,
  planFileShareRevocation,
  revokeFileShare,
  type FileShare,
  type FileSharePlan,
} from "@/appliance/file-shares";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";
import type { FileEntry } from "@/appliance/files";

type Pending =
  | { kind: "create"; plan: FileSharePlan; entry: FileEntry }
  | { kind: "revoke"; plan: FileSharePlan; share: FileShare };

export function FileSharePanel({
  selected,
  onClose,
}: {
  selected: FileEntry | null;
  onClose: () => void;
}) {
  const [shares, setShares] = useState<FileShare[]>([]);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createdUrl, setCreatedUrl] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listFileShares()
      .then(setShares)
      .catch((reason) => {
        setError(reason instanceof Error ? reason.message : "读取分享链接失败");
      });
  }, []);

  useEffect(refresh, [refresh]);

  const beginCreate = async () => {
    if (!selected || selected.kind !== "file") return;
    setBusy(true);
    setError(null);
    try {
      const plan = await planFileShare(selected.path);
      setPending({ kind: "create", plan, entry: selected });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "生成分享方案失败");
    } finally {
      setBusy(false);
    }
  };

  const beginRevoke = async (share: FileShare) => {
    setBusy(true);
    setError(null);
    try {
      const plan = await planFileShareRevocation(share.id);
      setPending({ kind: "revoke", plan, share });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "生成撤销方案失败");
    } finally {
      setBusy(false);
    }
  };

  const confirm = async (password: string) => {
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      const approval = await requestHighRiskApproval(
        pending.plan.approval.action,
        pending.plan.planId,
        password,
      );
      if (pending.kind === "create") {
        const result = await applyFileShare(
          pending.entry.path,
          pending.plan.planId,
          approval.approvalToken,
        );
        const absolute = new URL(result.url, window.location.origin).toString();
        setCreatedUrl(absolute);
        await navigator.clipboard?.writeText(absolute).catch(() => undefined);
      } else {
        await revokeFileShare(
          pending.share.id,
          pending.plan.planId,
          approval.approvalToken,
        );
      }
      setPending(null);
      refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "分享操作失败");
      throw reason;
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="absolute inset-0 z-[80] grid place-items-center bg-slate-950/25 p-6">
      <section className="w-full max-w-xl overflow-hidden rounded-2xl border border-white/60 bg-white shadow-2xl">
        <header className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <div>
            <h2 className="font-semibold text-slate-900">文件分享</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              链接默认 7 天失效，最多下载 100 次
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭文件分享">
            <XIcon className="size-5 text-slate-500" />
          </button>
        </header>

        <div className="space-y-4 p-5">
          <button
            type="button"
            disabled={!selected || selected.kind !== "file" || busy}
            onClick={() => void beginCreate()}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white disabled:bg-slate-300"
          >
            {busy ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <Link2Icon className="size-4" />
            )}
            {selected?.kind === "file"
              ? `分享“${selected.name}”`
              : "请先选择一个文件"}
          </button>

          {createdUrl && (
            <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3">
              <p className="text-xs font-medium text-emerald-800">
                链接已创建并尝试复制
              </p>
              <div className="mt-2 flex gap-2">
                <input
                  readOnly
                  value={createdUrl}
                  className="min-w-0 flex-1 rounded-lg border border-emerald-200 bg-white px-2 py-1.5 text-xs"
                />
                <button
                  type="button"
                  onClick={() =>
                    void navigator.clipboard?.writeText(createdUrl)
                  }
                  aria-label="复制分享链接"
                  className="rounded-lg bg-white p-2 text-emerald-700"
                >
                  <CopyIcon className="size-4" />
                </button>
              </div>
            </div>
          )}

          {error && (
            <p
              role="alert"
              className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700"
            >
              {error}
            </p>
          )}

          <div>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
              我的链接
            </h3>
            {shares.length === 0 ? (
              <p className="rounded-xl bg-slate-50 px-3 py-5 text-center text-sm text-slate-500">
                暂无分享链接
              </p>
            ) : (
              <ul className="max-h-64 space-y-2 overflow-y-auto">
                {shares.map((share) => (
                  <li
                    key={share.id}
                    className="flex items-center gap-3 rounded-xl border border-slate-200 px-3 py-2.5"
                  >
                    <Link2Icon className="size-4 shrink-0 text-blue-500" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-slate-800">
                        {share.filename}
                      </p>
                      <p className="text-xs text-slate-500">
                        {share.downloadCount}/{share.maxDownloads} 次 ·{" "}
                        {share.active
                          ? `${new Date(share.expiresAt * 1000).toLocaleString()} 到期`
                          : "已失效"}
                      </p>
                    </div>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void beginRevoke(share)}
                      title="立即撤销"
                      aria-label={`撤销 ${share.filename}`}
                      className="rounded-lg p-2 text-red-600 hover:bg-red-50"
                    >
                      <ShieldOffIcon className="size-4" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </section>

      <HighRiskApprovalDialog
        open={pending !== null}
        title={
          pending?.kind === "revoke" ? "撤销分享链接？" : "创建外部分享链接？"
        }
        description={
          pending?.kind === "revoke"
            ? "撤销后持有链接的人将立即无法下载。"
            : "任何持有此链接的人都可在有效期和次数限制内下载该文件。"
        }
        targetLabel={
          pending?.kind === "revoke"
            ? pending.share.filename
            : (pending?.entry.name ?? "文件")
        }
        confirmLabel={
          pending?.kind === "revoke" ? "立即撤销" : "创建并复制链接"
        }
        destructive={pending?.kind === "revoke"}
        onCancel={() => setPending(null)}
        onConfirm={confirm}
      />
    </div>
  );
}
