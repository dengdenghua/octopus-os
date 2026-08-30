import { useCallback, useEffect, useState } from "react";
import {
  CheckCircle2Icon,
  DownloadIcon,
  FingerprintIcon,
  KeyRoundIcon,
  Loader2Icon,
  RefreshCwIcon,
} from "lucide-react";

import {
  type AuditAnchor,
  type AuditKeyStatus,
  fetchAuditAnchor,
  fetchAuditKeyStatus,
  rotateAuditKey,
} from "@/appliance/audit";
import { requestHighRiskApproval } from "@/appliance/approval";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";

export function AuditEvidencePanel() {
  const [keys, setKeys] = useState<AuditKeyStatus | null>(null);
  const [anchor, setAnchor] = useState<AuditAnchor | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextKeys, nextAnchor] = await Promise.all([
        fetchAuditKeyStatus(),
        fetchAuditAnchor(),
      ]);
      setKeys(nextKeys);
      setAnchor(nextAnchor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取审计状态");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const rotate = async (password: string) => {
    setBusy(true);
    setError(null);
    try {
      const approval = await requestHighRiskApproval(
        "audit.key.rotate",
        "audit-chain",
        password,
      );
      await rotateAuditKey(approval.approvalToken);
      setDialogOpen(false);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法轮换审计密钥");
      throw reason;
    } finally {
      setBusy(false);
    }
  };

  const downloadAnchor = () => {
    if (!anchor) return;
    const blob = new Blob([JSON.stringify(anchor, null, 2) + "\n"], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `echo-audit-anchor-${new Date(anchor.createdAt)
      .toISOString()
      .replaceAll(":", "-")}.json`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <>
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-[24px] font-semibold tracking-tight">
            审计与证据
          </h1>
          <p className="mt-1 text-[13px] text-slate-500">
            验证管理操作记录，并把设备身份锚点保存到设备之外
          </p>
        </div>
        <button
          type="button"
          aria-label="刷新审计状态"
          disabled={loading || busy}
          onClick={() => void refresh()}
          className="grid size-9 place-items-center rounded-lg border border-slate-200 bg-white text-slate-600 shadow-sm disabled:opacity-40"
        >
          <RefreshCwIcon
            className={`size-4 ${loading ? "animate-spin" : ""}`}
          />
        </button>
      </header>

      {error && (
        <p
          role="alert"
          className="mt-4 rounded-xl bg-red-50 px-4 py-3 text-xs text-red-700"
        >
          {error}
        </p>
      )}

      {loading && !anchor ? (
        <div className="grid h-48 place-items-center text-slate-400">
          <Loader2Icon className="size-5 animate-spin" />
        </div>
      ) : (
        <>
          <section className="mt-6 rounded-2xl border border-slate-200/90 bg-white p-5 shadow-sm">
            <div className="flex items-start gap-4">
              <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-emerald-50 text-emerald-600">
                <CheckCircle2Icon className="size-5" />
              </span>
              <div className="min-w-0 flex-1">
                <h2 className="text-[15px] font-semibold">审计链健康</h2>
                <p className="mt-1 text-xs leading-5 text-slate-500">
                  已验证 {anchor?.audit.entries ?? 0} 条记录；当前尾序号为{" "}
                  {anchor?.audit.tailSeq ?? -1}。
                </p>
              </div>
            </div>
          </section>

          <section className="mt-4 rounded-2xl border border-slate-200/90 bg-white p-5 shadow-sm">
            <div className="flex items-start gap-4">
              <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-violet-50 text-violet-600">
                <FingerprintIcon className="size-5" />
              </span>
              <div className="min-w-0 flex-1">
                <h2 className="text-[15px] font-semibold">设备验证身份</h2>
                <p className="mt-1 break-all font-mono text-[10px] leading-4 text-slate-500">
                  {anchor?.signing.keyId || "尚未生成"}
                </p>
                <button
                  type="button"
                  disabled={!anchor}
                  onClick={downloadAnchor}
                  className="mt-3 inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 px-3 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40"
                >
                  <DownloadIcon className="size-3.5" />
                  下载签名锚点
                </button>
              </div>
            </div>
          </section>

          <section className="mt-4 flex items-center gap-4 rounded-2xl border border-slate-200/90 bg-white p-5 shadow-sm">
            <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-blue-50 text-blue-600">
              <KeyRoundIcon className="size-5" />
            </span>
            <div className="min-w-0 flex-1">
              <h2 className="text-[15px] font-semibold">审计签名密钥</h2>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                已使用 {keys?.keyCount ?? 1}/{keys?.maximumKeys ?? 64}{" "}
                个密钥；历史记录仍可验证。
              </p>
            </div>
            <button
              type="button"
              disabled={busy || !keys || keys.keyCount >= keys.maximumKeys}
              onClick={() => setDialogOpen(true)}
              className="h-9 shrink-0 rounded-lg border border-blue-200 bg-blue-50 px-3.5 text-[13px] font-medium text-blue-700 hover:bg-blue-100 disabled:opacity-40"
            >
              轮换密钥…
            </button>
          </section>

          <p className="mt-4 text-[11px] leading-5 text-slate-400">
            下载锚点不含审计正文。完整加密证据包由宿主定时任务写入外置盘或远端挂载；本机实时审计不会被保留策略删除。
          </p>
        </>
      )}

      <HighRiskApprovalDialog
        open={dialogOpen}
        title="轮换审计密钥？"
        description="将启用新的签名密钥并记录一次轮换事件；旧记录继续使用历史密钥验证。"
        targetLabel="设备审计链"
        confirmLabel="确认轮换"
        onCancel={() => setDialogOpen(false)}
        onConfirm={rotate}
      />
    </>
  );
}
