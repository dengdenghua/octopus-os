import { useCallback, useEffect, useState } from "react";
import { ActivityIcon, Loader2Icon, RefreshCwIcon } from "lucide-react";

import { requestHighRiskApproval } from "@/appliance/approval";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";
import {
  applyOmvSmartSelfTest,
  fetchOmvSmartSelfTest,
  planOmvSmartSelfTest,
  type OmvSmartSelfTestDesiredState,
  type OmvSmartSelfTestPlan,
  type OmvSmartSelfTestStatus,
} from "@/appliance/omv";

export function SmartSelfTestControls({ devicefile }: { devicefile: string }) {
  const [status, setStatus] = useState<OmvSmartSelfTestStatus | null>(null);
  const [pendingPlan, setPendingPlan] = useState<OmvSmartSelfTestPlan | null>(
    null,
  );
  const [pendingDesired, setPendingDesired] =
    useState<OmvSmartSelfTestDesiredState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setStatus(await fetchOmvSmartSelfTest(devicefile));
    } catch (reason) {
      setStatus(null);
      setError(
        reason instanceof Error ? reason.message : "无法读取 SMART 自检状态",
      );
    } finally {
      setLoading(false);
    }
  }, [devicefile]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const preview = async (test: "short" | "long") => {
    const desired: OmvSmartSelfTestDesiredState = {
      schema: "echo.omv.smart-self-test-desired.v1",
      devicefile,
      test,
    };
    setBusy(true);
    setError(null);
    try {
      const plan = await planOmvSmartSelfTest(desired);
      setPendingDesired(desired);
      setPendingPlan(plan);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法预览 SMART 自检",
      );
    } finally {
      setBusy(false);
    }
  };

  const apply = async (password: string) => {
    if (!pendingPlan || !pendingDesired) return;
    setBusy(true);
    setError(null);
    try {
      const approval = await requestHighRiskApproval(
        "storage.smart.self-test.start",
        pendingPlan.planId,
        password,
      );
      const result = await applyOmvSmartSelfTest(
        pendingDesired,
        pendingPlan.planId,
        approval.approvalToken,
      );
      if (result.selfTest) setStatus(result.selfTest);
      setPendingPlan(null);
      setPendingDesired(null);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法启动 SMART 自检",
      );
      throw reason;
    } finally {
      setBusy(false);
    }
  };

  const running = status?.state === "inProgress";
  return (
    <>
      <div className="col-span-3 mt-1 border-t border-slate-200 pt-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 text-[10px] text-slate-500">
            <ActivityIcon className="size-3.5" />
            {loading
              ? "正在读取自检状态…"
              : running
                ? `${status.kind === "long" ? "长时" : status.kind === "short" ? "短时" : "SMART"}自检进行中${status.progressPercent == null ? "" : ` · ${status.progressPercent}%`}`
                : status?.supported
                  ? "当前没有正在运行的 SMART 自检"
                  : "该磁盘未报告自检能力"}
          </div>
          <button
            type="button"
            aria-label="刷新 SMART 自检状态"
            disabled={loading || busy}
            onClick={() => void refresh()}
            className="rounded p-1 text-slate-400 hover:bg-slate-100 disabled:opacity-40"
          >
            {loading ? (
              <Loader2Icon className="size-3 animate-spin" />
            ) : (
              <RefreshCwIcon className="size-3" />
            )}
          </button>
        </div>
        {status?.supported && !running && (
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => void preview("short")}
              className="h-7 rounded-lg border border-slate-300 bg-white px-2.5 text-[10px] font-medium text-slate-700 disabled:opacity-40"
            >
              短时自检
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void preview("long")}
              className="h-7 rounded-lg border border-slate-300 bg-white px-2.5 text-[10px] font-medium text-slate-700 disabled:opacity-40"
            >
              长时自检
            </button>
          </div>
        )}
        {error && (
          <p role="alert" className="mt-2 text-[10px] text-red-700">
            {error}
          </p>
        )}
      </div>
      <HighRiskApprovalDialog
        open={Boolean(pendingPlan)}
        title={
          pendingDesired?.test === "long"
            ? "确认启动长时 SMART 自检"
            : "确认启动短时 SMART 自检"
        }
        description="自检在磁盘后台运行，可能增加 I/O 延迟；不会使用 captive 模式，也不提供远程中止操作。请输入管理员密码继续。"
        targetLabel={devicefile}
        confirmLabel="确认启动"
        onCancel={() => {
          setPendingPlan(null);
          setPendingDesired(null);
        }}
        onConfirm={apply}
      />
    </>
  );
}
