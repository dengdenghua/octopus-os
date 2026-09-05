import { useCallback, useEffect, useState } from "react";
import { CalendarClockIcon, Loader2Icon } from "lucide-react";

import { requestHighRiskApproval } from "@/appliance/approval";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";
import { fetchNativeStatus } from "@/appliance/omv";
import {
  applySmartSchedule,
  fetchSmartSchedule,
  planSmartSchedule,
  type SmartScheduleDesired,
  type SmartSchedulePlan,
  type SmartScheduleStatus,
} from "@/appliance/smart-schedule";

const CAPABILITY = "storage.smart.self-test.schedule.v1";

export function SmartSchedulePanel() {
  const [status, setStatus] = useState<SmartScheduleStatus | null>(null);
  const [capable, setCapable] = useState(false);
  const [pendingDesired, setPendingDesired] =
    useState<SmartScheduleDesired | null>(null);
  const [pendingPlan, setPendingPlan] = useState<SmartSchedulePlan | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextStatus, native] = await Promise.all([
        fetchSmartSchedule(),
        fetchNativeStatus(),
      ]);
      setStatus(nextStatus);
      setCapable(native.capabilities.includes(CAPABILITY));
    } catch (reason) {
      setStatus(null);
      setError(
        reason instanceof Error
          ? reason.message
          : "无法读取 SMART 定时自检策略",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const preview = async (enabled: boolean) => {
    const desired: SmartScheduleDesired = {
      schema: "echo.smart-self-test-schedule-desired.v1",
      enabled,
    };
    setBusy(true);
    setError(null);
    try {
      const plan = await planSmartSchedule(desired);
      if (plan.operation === "none") {
        await refresh();
        return;
      }
      setPendingDesired(desired);
      setPendingPlan(plan);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "无法预览 SMART 定时自检策略",
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
        "storage.smart.self-test.schedule",
        pendingPlan.planId,
        password,
      );
      await applySmartSchedule(
        pendingDesired,
        pendingPlan.planId,
        approval.approvalToken,
      );
      setPendingDesired(null);
      setPendingPlan(null);
      await refresh();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "无法更新 SMART 定时自检策略",
      );
      throw reason;
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <section className="mt-5 rounded-[22px] bg-white/80 p-5 shadow-sm ring-1 ring-white/90">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <CalendarClockIcon className="size-5 text-blue-600" /> 定时 SMART
              短检
            </h2>
            <p className="mt-1 max-w-2xl text-[11px] leading-5 text-slate-500">
              {status?.enabled
                ? "已启用：每周日 03:30（本机时间）检查所有整盘，并随机延后最多 30 分钟。已有自检会跳过。"
                : "默认关闭。启用后只运行非 captive 的短时自检；任务使用低 I/O 优先级，不会自动启动长检或中止现有自检。"}
            </p>
          </div>
          <button
            type="button"
            disabled={loading || busy || !status || !capable}
            onClick={() => status && void preview(!status.enabled)}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 text-[11px] font-semibold text-slate-700 disabled:opacity-40"
          >
            {(loading || busy) && (
              <Loader2Icon className="size-3 animate-spin" />
            )}
            {status?.enabled ? "停用定时短检" : "启用定时短检"}
          </button>
        </div>
        {!loading && !capable && (
          <p className="mt-3 rounded-xl bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
            本机缺少 smartctl，定时自检不可用。
          </p>
        )}
        {error && (
          <p role="alert" className="mt-3 text-[11px] text-red-700">
            {error}
          </p>
        )}
      </section>
      <HighRiskApprovalDialog
        open={Boolean(pendingPlan)}
        title={
          pendingDesired?.enabled ? "确认启用定时短检" : "确认停用定时短检"
        }
        description="此操作会更新 root 管理的本机策略。启用后，systemd 每周触发一次整盘 SMART 短检，不会自动启动长检；请输入管理员密码继续。"
        targetLabel="所有已枚举整盘 · SMART short"
        confirmLabel={pendingDesired?.enabled ? "确认启用" : "确认停用"}
        onCancel={() => {
          setPendingDesired(null);
          setPendingPlan(null);
        }}
        onConfirm={apply}
      />
    </>
  );
}
