import { useCallback, useEffect, useState } from "react";
import { HardDriveDownloadIcon, Loader2Icon } from "lucide-react";

import { requestHighRiskApproval } from "@/appliance/approval";
import {
  applyDiskIdle,
  fetchDiskIdle,
  planDiskIdle,
  type DiskIdleDesired,
  type DiskIdlePlan,
  type DiskIdleStatus,
} from "@/appliance/disk-idle";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";
import { fetchNativeStatus } from "@/appliance/omv";

const CAPABILITY = "storage.disk.idle.configure.v1";
const CHOICES = [0, 30, 60, 120, 240] as const;

function label(minutes: number) {
  if (minutes === 0) return "关闭";
  if (minutes < 60) return `${minutes} 分钟`;
  return `${minutes / 60} 小时`;
}

export function DiskIdlePanel() {
  const [status, setStatus] = useState<DiskIdleStatus | null>(null);
  const [capable, setCapable] = useState(false);
  const [selected, setSelected] = useState<DiskIdleDesired["idleMinutes"]>(0);
  const [pendingDesired, setPendingDesired] = useState<DiskIdleDesired | null>(
    null,
  );
  const [pendingPlan, setPendingPlan] = useState<DiskIdlePlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextStatus, native] = await Promise.all([
        fetchDiskIdle(),
        fetchNativeStatus(),
      ]);
      setStatus(nextStatus);
      setSelected(nextStatus.idleMinutes);
      setCapable(native.capabilities.includes(CAPABILITY));
    } catch (reason) {
      setStatus(null);
      setError(
        reason instanceof Error ? reason.message : "无法读取磁盘休眠策略",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const preview = async () => {
    const desired: DiskIdleDesired = {
      schema: "echo.disk-idle-policy-desired.v1",
      idleMinutes: selected,
    };
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      const plan = await planDiskIdle(desired);
      if (plan.operation === "none") {
        setSuccess("磁盘休眠策略未变化");
        return;
      }
      setPendingDesired(desired);
      setPendingPlan(plan);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法预览磁盘休眠策略",
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
        "storage.disk.idle.configure",
        pendingPlan.planId,
        password,
      );
      const result = await applyDiskIdle(
        pendingDesired,
        pendingPlan.planId,
        approval.approvalToken,
      );
      setPendingDesired(null);
      setPendingPlan(null);
      await refresh();
      setSuccess(
        pendingDesired.idleMinutes === 0
          ? "磁盘自动休眠已关闭"
          : `已向 ${result.hardwareUpdated ?? result.devices.length} 块磁盘下发 ${label(pendingDesired.idleMinutes)}休眠计时`,
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法更新磁盘休眠策略",
      );
      throw reason;
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <section className="mt-5 rounded-[22px] bg-white/80 p-5 shadow-sm ring-1 ring-white/90">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-2xl">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <HardDriveDownloadIcon className="size-5 text-indigo-600" /> 内置
              HDD 自动休眠
            </h2>
            <p className="mt-1 text-[11px] leading-5 text-slate-500">
              仅作用于具备稳定身份的内置 ATA/SATA
              机械整盘；NVMe、USB、可移动盘和身份不明磁盘不会被修改。持续 I/O
              会阻止休眠，部分硬盘固件也可能忽略计时器。
            </p>
            <p className="mt-2 text-[11px] font-medium text-slate-700">
              当前：{status ? label(status.idleMinutes) : "读取中"} · 可用磁盘{" "}
              {status?.eligibleDeviceCount ?? 0} 块
            </p>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-[11px] font-medium text-slate-600">
              空闲时长
              <select
                aria-label="磁盘空闲时长"
                value={selected}
                disabled={loading || busy || !capable}
                onChange={(event) =>
                  setSelected(
                    Number(
                      event.target.value,
                    ) as DiskIdleDesired["idleMinutes"],
                  )
                }
                className="ml-2 h-8 rounded-lg border border-slate-300 bg-white px-2 text-[11px]"
              >
                {CHOICES.map((minutes) => (
                  <option key={minutes} value={minutes}>
                    {label(minutes)}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              disabled={
                loading ||
                busy ||
                !status ||
                !capable ||
                selected === status.idleMinutes
              }
              onClick={() => void preview()}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-indigo-700 px-3 text-[11px] font-semibold text-white disabled:opacity-40"
            >
              {busy && <Loader2Icon className="size-3 animate-spin" />} 预览更新
            </button>
          </div>
        </div>
        {!loading && !capable && (
          <p className="mt-3 rounded-xl bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
            本机缺少 hdparm、lsblk 或受信任的开机应用服务，磁盘休眠不可用。
          </p>
        )}
        <p className="mt-3 rounded-xl bg-slate-100 px-3 py-2 text-[11px] leading-5 text-slate-600">
          回读只能确认系统接受命令和策略已持久化，不能保证每款硬盘固件最终进入
          standby；过短休眠会增加启停次数，因此最低为 30 分钟。
        </p>
        {error && (
          <p role="alert" className="mt-3 text-[11px] text-red-700">
            {error}
          </p>
        )}
        {success && (
          <p className="mt-3 text-[11px] text-emerald-700">{success}</p>
        )}
      </section>
      <HighRiskApprovalDialog
        open={Boolean(pendingPlan)}
        title={
          pendingDesired?.idleMinutes === 0
            ? "确认关闭磁盘休眠"
            : "确认更新磁盘休眠"
        }
        description={`此操作会向预览绑定的 ${pendingPlan?.devices.length ?? 0} 块内置机械盘下发 standby timer，并更新开机策略。磁盘身份变化会使预览失效。`}
        targetLabel={`${pendingPlan?.devices.length ?? 0} 块稳定身份 ATA/SATA HDD · ${label(pendingDesired?.idleMinutes ?? 0)}`}
        confirmLabel="确认更新"
        onCancel={() => {
          setPendingDesired(null);
          setPendingPlan(null);
        }}
        onConfirm={apply}
      />
    </>
  );
}
