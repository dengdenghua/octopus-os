import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangleIcon,
  BatteryChargingIcon,
  Loader2Icon,
  PlugZapIcon,
  RefreshCwIcon,
} from "lucide-react";

import {
  fetchOmvUpsStatus,
  type OmvUpsDevice,
  type OmvUpsSnapshot,
} from "@/appliance/omv";

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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSnapshot(await fetchOmvUpsStatus());
    } catch (reason) {
      setSnapshot(null);
      setError(reason instanceof Error ? reason.message : "无法读取 UPS 状态");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <section className="mt-5 rounded-[22px] bg-white/80 p-5 shadow-sm ring-1 ring-white/90">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-900">
            <PlugZapIcon className="size-5 text-indigo-600" /> UPS 电源保护
          </h2>
          <p className="mt-1 text-[11px] leading-5 text-slate-500">
            只读显示本机 NUT 服务状态；不会读取 UPS 序列号、凭据或远端设备。
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
                    {device.manufacturer || "UPS"} {device.model || device.name}
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
    </section>
  );
}
