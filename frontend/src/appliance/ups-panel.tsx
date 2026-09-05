import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangleIcon,
  BatteryChargingIcon,
  Loader2Icon,
  PlugZapIcon,
  RefreshCwIcon,
} from "lucide-react";

import {
  applyOmvUpsShutdownPolicy,
  fetchOmvStatus,
  fetchOmvUpsShutdownPolicy,
  fetchOmvUpsStatus,
  planOmvUpsShutdownPolicy,
  type OmvUpsDevice,
  type OmvUpsShutdownPolicy,
  type OmvUpsShutdownPolicyDesiredState,
  type OmvUpsShutdownPolicyPlan,
  type OmvUpsSnapshot,
} from "@/appliance/omv";
import { requestHighRiskApproval } from "@/appliance/approval";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";

function runtimeLabel(seconds: number | null) {
  if (seconds === null) return "续航未知";
  const minutes = Math.max(0, Math.floor(seconds / 60));
  if (minutes < 60) return `约 ${minutes} 分钟`;
  return `约 ${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`;
}

function stateLabel(device: OmvUpsDevice) {
  return {
    online: "市电在线",
    onBattery: "电池供电",
    lowBattery: "电量不足",
    replaceBattery: "需要更换电池",
    shutdownPending: "关机信号已触发",
    offline: "UPS 已关闭",
    unknown: "状态未知",
  }[device.state];
}

function stateStyle(device: OmvUpsDevice) {
  if (device.state === "online") return "bg-emerald-100 text-emerald-800";
  if (device.state === "onBattery") return "bg-amber-100 text-amber-900";
  return "bg-red-100 text-red-800";
}

export function UpsPanel() {
  const [snapshot, setSnapshot] = useState<OmvUpsSnapshot | null>(null);
  const [policy, setPolicy] = useState<OmvUpsShutdownPolicy | null>(null);
  const [policyCapable, setPolicyCapable] = useState(false);
  const [sampleCount, setSampleCount] = useState(3);
  const [pendingDesired, setPendingDesired] =
    useState<OmvUpsShutdownPolicyDesiredState | null>(null);
  const [pendingPlan, setPendingPlan] =
    useState<OmvUpsShutdownPolicyPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [policyBusy, setPolicyBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextSnapshot, nextPolicy, status] = await Promise.all([
        fetchOmvUpsStatus(),
        fetchOmvUpsShutdownPolicy(),
        fetchOmvStatus(),
      ]);
      setSnapshot(nextSnapshot);
      setPolicy(nextPolicy);
      setSampleCount(nextPolicy.requiredConsecutiveSamples);
      setPolicyCapable(
        status.capabilities.includes("power.ups-shutdown-policy.v1"),
      );
    } catch (reason) {
      setSnapshot(null);
      setError(reason instanceof Error ? reason.message : "无法读取 UPS 状态");
    } finally {
      setLoading(false);
    }
  }, []);

  const previewPolicy = async (enabled: boolean) => {
    const desired: OmvUpsShutdownPolicyDesiredState = {
      schema: "echo.ups-shutdown-policy-desired.v1",
      enabled,
      requiredConsecutiveSamples: sampleCount,
    };
    setPolicyBusy(true);
    setError(null);
    try {
      const plan = await planOmvUpsShutdownPolicy(desired);
      if (plan.operation === "none") {
        await refresh();
        return;
      }
      setPendingDesired(desired);
      setPendingPlan(plan);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法预览 UPS 自动关机策略",
      );
    } finally {
      setPolicyBusy(false);
    }
  };

  const applyPolicy = async (password: string) => {
    if (!pendingDesired || !pendingPlan) return;
    setPolicyBusy(true);
    setError(null);
    try {
      const approval = await requestHighRiskApproval(
        "power.ups-shutdown-policy.set",
        pendingPlan.planId,
        password,
      );
      await applyOmvUpsShutdownPolicy(
        pendingDesired,
        pendingPlan.planId,
        approval.approvalToken,
      );
      setPendingDesired(null);
      setPendingPlan(null);
      await refresh();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法更新 UPS 自动关机策略",
      );
      throw reason;
    } finally {
      setPolicyBusy(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <>
      <section className="mt-5 rounded-[22px] bg-white/80 p-5 shadow-sm ring-1 ring-white/90">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <PlugZapIcon className="size-5 text-indigo-600" /> UPS 电源保护
            </h2>
            <p className="mt-1 text-[11px] leading-5 text-slate-500">
              只连接本机 NUT；不会读取 UPS 序列号、凭据或远端设备。
            </p>
          </div>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={loading}
            aria-label="刷新 UPS 状态"
            className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 disabled:opacity-50"
          >
            {loading ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <RefreshCwIcon className="size-4" />
            )}
          </button>
        </div>

        {error ? (
          <div
            role="alert"
            className="mt-4 rounded-xl bg-red-50 p-3 text-xs text-red-700"
          >
            {error}
          </div>
        ) : snapshot?.state === "unavailable" ? (
          <div className="mt-4 flex gap-3 rounded-xl bg-amber-50 p-3 text-amber-900">
            <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
            <p className="text-xs leading-5">
              {snapshot.code === "toolMissing"
                ? "系统尚未安装 NUT 客户端，UPS 状态不可观测。"
                : "NUT 服务或已登记 UPS 当前不可用，请检查本机服务。"}
            </p>
          </div>
        ) : snapshot?.state === "notConfigured" ? (
          <p className="mt-4 rounded-xl bg-slate-50 p-3 text-xs leading-5 text-slate-600">
            NUT 客户端可用，但尚未登记 UPS。当前不会自动执行低电量关机。
          </p>
        ) : (
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {snapshot?.devices.map((device) => (
              <article
                key={device.name}
                className="rounded-2xl bg-slate-50/90 p-4 ring-1 ring-slate-200"
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h3 className="text-xs font-semibold text-slate-900">
                      {device.manufacturer || "UPS"}{" "}
                      {device.model || device.name}
                    </h3>
                    <p className="mt-1 font-mono text-[10px] text-slate-400">
                      {device.name}
                    </p>
                  </div>
                  <span
                    className={`rounded-full px-2 py-1 text-[10px] font-semibold ${stateStyle(device)}`}
                  >
                    {stateLabel(device)}
                  </span>
                </div>
                <div className="mt-4 flex items-end gap-3">
                  <BatteryChargingIcon className="size-6 text-indigo-600" />
                  <strong className="text-2xl font-semibold text-slate-900">
                    {device.chargePercent === null
                      ? "—"
                      : `${device.chargePercent}%`}
                  </strong>
                  <span className="pb-1 text-[11px] text-slate-500">
                    {runtimeLabel(device.runtimeSeconds)}
                  </span>
                </div>
                <dl className="mt-4 grid grid-cols-2 gap-2 text-[10px]">
                  <div className="rounded-lg bg-white p-2">
                    <dt className="text-slate-400">负载</dt>
                    <dd className="mt-1 font-semibold text-slate-700">
                      {device.loadPercent === null
                        ? "未知"
                        : `${device.loadPercent}%`}
                    </dd>
                  </div>
                  <div className="rounded-lg bg-white p-2">
                    <dt className="text-slate-400">温度</dt>
                    <dd className="mt-1 font-semibold text-slate-700">
                      {device.temperatureC === null
                        ? "未知"
                        : `${device.temperatureC}°C`}
                    </dd>
                  </div>
                  <div className="rounded-lg bg-white p-2">
                    <dt className="text-slate-400">输入电压</dt>
                    <dd className="mt-1 font-semibold text-slate-700">
                      {device.inputVoltage === null
                        ? "未知"
                        : `${device.inputVoltage} V`}
                    </dd>
                  </div>
                  <div className="rounded-lg bg-white p-2">
                    <dt className="text-slate-400">输出电压</dt>
                    <dd className="mt-1 font-semibold text-slate-700">
                      {device.outputVoltage === null
                        ? "未知"
                        : `${device.outputVoltage} V`}
                    </dd>
                  </div>
                </dl>
              </article>
            ))}
          </div>
        )}

        {policy && (
          <div className="mt-4 rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="text-xs font-semibold text-slate-900">
                  低电量自动关机
                </h3>
                <p className="mt-1 max-w-xl text-[11px] leading-5 text-slate-500">
                  {policy.enabled
                    ? `已启用：连续 ${policy.requiredConsecutiveSamples} 次检测到电池供电且低电量后关机；NUT FSD 信号会立即保护关机。`
                    : "默认关闭。启用后仅响应本机 NUT 的 OB+LB 或 FSD 状态，没有远程关机入口。"}
                </p>
              </div>
              <span
                className={`rounded-full px-2.5 py-1 text-[10px] font-semibold ${
                  policy.enabled
                    ? "bg-emerald-100 text-emerald-800"
                    : "bg-slate-200 text-slate-600"
                }`}
              >
                {policy.enabled ? "保护已开启" : "保护已关闭"}
              </span>
            </div>
            <div className="mt-3 flex flex-wrap items-end gap-2">
              <label className="text-[11px] text-slate-600">
                连续确认次数
                <select
                  aria-label="低电量连续确认次数"
                  value={sampleCount}
                  disabled={!policyCapable || policyBusy}
                  onChange={(event) =>
                    setSampleCount(Number(event.currentTarget.value))
                  }
                  className="ml-2 h-8 rounded-lg border border-slate-300 bg-white px-2 text-xs"
                >
                  {Array.from({ length: 11 }, (_, index) => index + 2).map(
                    (value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ),
                  )}
                </select>
              </label>
              {policy.enabled &&
                sampleCount !== policy.requiredConsecutiveSamples && (
                  <button
                    type="button"
                    disabled={!policyCapable || policyBusy}
                    onClick={() => void previewPolicy(true)}
                    className="h-8 rounded-lg bg-indigo-600 px-3 text-[11px] font-semibold text-white disabled:opacity-40"
                  >
                    保存确认次数
                  </button>
                )}
              <button
                type="button"
                disabled={
                  !policyCapable ||
                  policyBusy ||
                  (!policy.enabled &&
                    (!snapshot?.configured || !snapshot.available))
                }
                onClick={() => void previewPolicy(!policy.enabled)}
                className={`h-8 rounded-lg px-3 text-[11px] font-semibold text-white disabled:opacity-40 ${
                  policy.enabled ? "bg-slate-600" : "bg-blue-600"
                }`}
              >
                {policy.enabled ? "关闭自动关机" : "启用低电量关机"}
              </button>
            </div>
            {!policyCapable && (
              <p className="mt-2 text-[10px] text-amber-700">
                当前系统缺少本机 NUT 或 systemd 能力，策略不可编辑。
              </p>
            )}
          </div>
        )}
      </section>
      <HighRiskApprovalDialog
        open={Boolean(pendingPlan)}
        title={
          pendingDesired?.enabled
            ? "确认启用 UPS 自动关机"
            : "确认关闭 UPS 自动关机"
        }
        description={
          pendingDesired?.enabled
            ? `计划要求连续 ${pendingDesired.requiredConsecutiveSamples} 次低电量确认；FSD 会立即请求系统安全关机。请输入管理员密码继续。`
            : "关闭后，UPS 低电量与 FSD 信号都不会再触发 Echo OS 自动关机。请输入管理员密码继续。"
        }
        targetLabel={pendingPlan?.planId}
        confirmLabel={pendingDesired?.enabled ? "确认启用" : "确认关闭"}
        destructive={Boolean(pendingDesired?.enabled)}
        onCancel={() => {
          setPendingDesired(null);
          setPendingPlan(null);
        }}
        onConfirm={applyPolicy}
      />
    </>
  );
}
