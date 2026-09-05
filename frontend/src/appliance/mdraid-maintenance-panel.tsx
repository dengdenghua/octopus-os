import { useCallback, useEffect, useState } from "react";
import {
  ActivityIcon,
  CheckCircle2Icon,
  Loader2Icon,
  RefreshCwIcon,
} from "lucide-react";

import { requestHighRiskApproval } from "@/appliance/approval";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";
import {
  applyMdRaidCheck,
  applyMdRaidCheckSchedule,
  fetchMdRaidCheckSchedule,
  fetchMdRaidMaintenance,
  planMdRaidCheck,
  planMdRaidCheckSchedule,
  type MdRaidCheckDesired,
  type MdRaidCheckPlan,
  type MdRaidCheckSchedule,
  type MdRaidCheckScheduleDesired,
  type MdRaidCheckSchedulePlan,
  type MdRaidMaintenance,
} from "@/appliance/mdraid-maintenance";
import { fetchNativeStatus } from "@/appliance/omv";
import { cn } from "@/lib/utils";

const CHECK_CAPABILITY = "storage.array.mdraid.check.start.v1";
const SCHEDULE_CAPABILITY = "storage.array.mdraid.check.schedule.v1";

function actionLabel(item: MdRaidMaintenance) {
  if (item.action === "idle")
    return item.mismatchCount > 0 ? "发现不一致" : "空闲";
  const progress =
    item.progressPercent == null ? "" : ` · ${item.progressPercent}%`;
  return `${item.action}${progress}`;
}

export function MdRaidMaintenancePanel() {
  const [arrays, setArrays] = useState<MdRaidMaintenance[]>([]);
  const [schedule, setSchedule] = useState<MdRaidCheckSchedule | null>(null);
  const [checkAvailable, setCheckAvailable] = useState(false);
  const [scheduleAvailable, setScheduleAvailable] = useState(false);
  const [checkPlan, setCheckPlan] = useState<MdRaidCheckPlan | null>(null);
  const [schedulePlan, setSchedulePlan] =
    useState<MdRaidCheckSchedulePlan | null>(null);
  const [scheduleDesired, setScheduleDesired] =
    useState<MdRaidCheckScheduleDesired | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const status = await fetchNativeStatus();
      const canCheck = status.capabilities?.includes(CHECK_CAPABILITY) ?? false;
      const canSchedule =
        status.capabilities?.includes(SCHEDULE_CAPABILITY) ?? false;
      setCheckAvailable(canCheck);
      setScheduleAvailable(canSchedule);
      const [nextArrays, nextSchedule] = await Promise.all([
        canCheck ? fetchMdRaidMaintenance() : Promise.resolve([]),
        canSchedule ? fetchMdRaidCheckSchedule() : Promise.resolve(null),
      ]);
      setArrays(nextArrays);
      setSchedule(nextSchedule);
    } catch (reason) {
      setArrays([]);
      setSchedule(null);
      setError(
        reason instanceof Error ? reason.message : "无法读取 RAID1 维护状态",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const previewCheck = async (item: MdRaidMaintenance) => {
    const desired: MdRaidCheckDesired = {
      schema: "echo.omv.mdraid-check-desired.v1",
      name: item.array.name,
      arrayUuid: item.array.uuid,
      operation: "start",
    };
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      setSchedulePlan(null);
      setCheckPlan(await planMdRaidCheck(desired));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法预览 RAID1 校验",
      );
    } finally {
      setBusy(false);
    }
  };

  const previewSchedule = async () => {
    const desired: MdRaidCheckScheduleDesired = {
      schema: "echo.mdraid-check-schedule-desired.v1",
      enabled: !(schedule?.enabled ?? false),
    };
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      setCheckPlan(null);
      const plan = await planMdRaidCheckSchedule(desired);
      if (plan.operation === "none") {
        setSuccess("RAID1 月度校验策略未变化");
        return;
      }
      setScheduleDesired(desired);
      setSchedulePlan(plan);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法预览月度校验策略",
      );
    } finally {
      setBusy(false);
    }
  };

  const confirmCheck = async (password: string) => {
    if (!checkPlan) return;
    const approval = await requestHighRiskApproval(
      "omv.mdraid.check.start",
      checkPlan.planId,
      password,
    );
    const result = await applyMdRaidCheck(
      checkPlan.desired,
      checkPlan.planId,
      approval.approvalToken,
    );
    setCheckPlan(null);
    setSuccess(
      result.maintenanceState === "checking"
        ? `${result.array.devicefile} 一致性校验已启动`
        : `${result.array.devicefile} 一致性校验已完成`,
    );
    await refresh();
  };

  const confirmSchedule = async (password: string) => {
    if (!schedulePlan || !scheduleDesired) return;
    const approval = await requestHighRiskApproval(
      "storage.mdraid.check.schedule",
      schedulePlan.planId,
      password,
    );
    await applyMdRaidCheckSchedule(
      scheduleDesired,
      schedulePlan.planId,
      approval.approvalToken,
    );
    const enabled = scheduleDesired.enabled;
    setSchedulePlan(null);
    setScheduleDesired(null);
    setSuccess(enabled ? "RAID1 月度校验已启用" : "RAID1 月度校验已关闭");
    await refresh();
  };

  return (
    <>
      <section className="mt-4 rounded-[22px] bg-white/78 p-5 shadow-sm ring-1 ring-white/90">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <ActivityIcon className="size-5 text-blue-600" /> RAID1 一致性校验
            </h2>
            <p className="mt-2 max-w-2xl text-[11px] leading-5 text-slate-500">
              仅检查健康的 Echo 自管双盘 RAID1，不启动显式
              repair、不改变成员关系。 校验会产生较高
              I/O；底层遇到读错误时仍可能由镜像冗余自动恢复读取。
            </p>
          </div>
          <button
            type="button"
            aria-label="刷新 RAID1 校验状态"
            onClick={() => void refresh()}
            disabled={loading || busy}
            className="grid size-8 place-items-center rounded-full bg-slate-50 text-slate-500 ring-1 ring-slate-200 disabled:opacity-40"
          >
            <RefreshCwIcon
              className={cn("size-3.5", loading && "animate-spin")}
            />
          </button>
        </div>

        {error && (
          <p role="alert" className="mt-3 text-xs text-red-700">
            {error}
          </p>
        )}
        {success && (
          <p
            role="status"
            className="mt-3 flex items-center gap-2 text-xs text-emerald-700"
          >
            <CheckCircle2Icon className="size-4" /> {success}
          </p>
        )}

        {scheduleAvailable && schedule && (
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 px-3 py-2.5">
            <div>
              <p className="text-xs font-semibold text-slate-700">
                月度自动校验：{schedule.enabled ? "已启用" : "已关闭"}
              </p>
              <p className="mt-1 text-[10px] text-slate-400">
                每月首个周日 00:45 后随机延迟最多 24 小时；降级或忙碌阵列会跳过
              </p>
            </div>
            <button
              type="button"
              onClick={() => void previewSchedule()}
              disabled={busy || loading}
              className="h-8 rounded-lg bg-slate-900 px-3 text-[11px] font-semibold text-white disabled:opacity-40"
            >
              预览{schedule.enabled ? "关闭" : "启用"}月度校验
            </button>
          </div>
        )}

        {loading && arrays.length === 0 ? (
          <p className="mt-4 flex items-center gap-2 text-xs text-slate-400">
            <Loader2Icon className="size-4 animate-spin" />{" "}
            正在读取阵列维护状态…
          </p>
        ) : !checkAvailable ? (
          <p className="mt-4 rounded-xl bg-slate-50 p-3 text-xs text-slate-500">
            当前主机未提供受控 RAID1 一致性校验能力。
          </p>
        ) : arrays.length === 0 ? (
          <p className="mt-4 rounded-xl bg-slate-50 p-3 text-xs text-slate-500">
            当前没有 Echo 自管 RAID1。
          </p>
        ) : (
          <div className="mt-4 space-y-2">
            {arrays.map((item) => (
              <article
                key={item.array.uuid}
                className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 px-3 py-3"
              >
                <div>
                  <p className="text-xs font-semibold text-slate-800">
                    {item.array.devicefile}
                  </p>
                  <p className="mt-1 text-[10px] text-slate-500">
                    状态：{actionLabel(item)} · mismatch {item.mismatchCount}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void previewCheck(item)}
                  disabled={!item.canStartCheck || busy}
                  className="h-8 rounded-lg bg-blue-600 px-3 text-[11px] font-semibold text-white disabled:opacity-40"
                >
                  预览一致性校验
                </button>
              </article>
            ))}
          </div>
        )}
      </section>

      <HighRiskApprovalDialog
        open={Boolean(checkPlan)}
        title="确认启动 RAID1 一致性校验"
        description="校验不可在接受后自动回滚，会产生较高磁盘 I/O；服务端执行前将重新核验阵列身份、健康和空闲状态。"
        targetLabel={checkPlan?.array.devicefile}
        confirmLabel="启动一致性校验"
        onCancel={() => setCheckPlan(null)}
        onConfirm={confirmCheck}
      />
      <HighRiskApprovalDialog
        open={Boolean(schedulePlan)}
        title={
          scheduleDesired?.enabled ? "确认启用月度校验" : "确认关闭月度校验"
        }
        description="定时任务只扫描健康、空闲的 Echo 自管双盘 RAID1；不会启动显式 repair。"
        targetLabel="每月首个周日 · 随机延迟最多 24 小时"
        confirmLabel="确认更新月度校验"
        onCancel={() => {
          setSchedulePlan(null);
          setScheduleDesired(null);
        }}
        onConfirm={confirmSchedule}
      />
    </>
  );
}
