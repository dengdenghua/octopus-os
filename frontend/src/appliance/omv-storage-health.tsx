import { useEffect, useState } from "react";
import {
  ArrowRightIcon,
  CheckCircle2Icon,
  HardDriveIcon,
  Layers3Icon,
  Loader2Icon,
  RefreshCwIcon,
  ShieldCheckIcon,
  ThermometerIcon,
  TriangleAlertIcon,
} from "lucide-react";

import {
  fetchOmvFilesystems,
  fetchOmvHealth,
  fetchOmvSmart,
  fetchOmvSmartDevices,
  fetchOmvStorageTopology,
  fetchOmvStatus,
  type OmvFilesystem,
  type OmvHealthSnapshot,
  type OmvSmart,
  type OmvSmartDevice,
  type OmvStorageTopology,
  type OmvStatus,
} from "@/appliance/omv";

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB", "PB"];
  let value = bytes;
  let unit = -1;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 100 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}

function healthy(value: string) {
  return /^(passed|ok|good|healthy|true)$/i.test(value.trim());
}

function raidStatus(status: string) {
  const labels: Record<string, string> = {
    healthy: "正常",
    degraded: "已降级",
    recovering: "正在重建",
    checking: "正在校验",
    inactive: "未激活",
    unknown: "状态未知",
  };
  return labels[status] || status;
}

function topologyKind(type: string) {
  if (/^raid|^md$/i.test(type)) return "阵列";
  if (/^lvm$/i.test(type)) return "LVM";
  if (/^crypt$/i.test(type)) return "加密层";
  return type.toUpperCase();
}

function formatTimestamp(value: string | null) {
  if (!value) return "尚未完成";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "时间未知";
  return parsed.toLocaleString("zh-CN", { hour12: false });
}

export function OmvStorageHealth() {
  const [reloadKey, setReloadKey] = useState(0);
  const [status, setStatus] = useState<OmvStatus | null>(null);
  const [filesystems, setFilesystems] = useState<OmvFilesystem[]>([]);
  const [devices, setDevices] = useState<OmvSmartDevice[]>([]);
  const [topology, setTopology] = useState<OmvStorageTopology | null>(null);
  const [healthSnapshot, setHealthSnapshot] =
    useState<OmvHealthSnapshot | null>(null);
  const [smart, setSmart] = useState<Record<string, OmvSmart>>({});
  const [smartLoading, setSmartLoading] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    setSmart({});
    setHealthSnapshot(null);
    fetchOmvStatus()
      .then(async (nextStatus) => {
        if (!alive) return;
        setStatus(nextStatus);
        if (!nextStatus.configured) {
          setFilesystems([]);
          setDevices([]);
          setTopology(null);
          return;
        }
        const nextHealth = await fetchOmvHealth();
        if (!alive) return;
        setHealthSnapshot(nextHealth);
        if (!nextStatus.available) {
          setFilesystems([]);
          setDevices([]);
          setTopology(null);
          return;
        }
        const [volumes, physicalDevices, storageTopology] = await Promise.all([
          fetchOmvFilesystems(),
          fetchOmvSmartDevices(),
          fetchOmvStorageTopology(),
        ]);
        if (alive) {
          setFilesystems(volumes);
          setDevices(physicalDevices);
          setTopology(storageTopology);
        }
      })
      .catch((reason) => {
        if (alive) {
          setError(
            reason instanceof Error ? reason.message : "无法读取存储健康状态",
          );
          setFilesystems([]);
          setDevices([]);
          setTopology(null);
          setHealthSnapshot(null);
        }
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [reloadKey]);

  const readSmart = async (device: string) => {
    setSmartLoading(device);
    setError(null);
    try {
      const report = await fetchOmvSmart(device);
      setSmart((current) => ({ ...current, [device]: report }));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法读取 SMART 状态",
      );
    } finally {
      setSmartLoading(null);
    }
  };

  const topologyLayers = (topology?.devices ?? []).filter((device) =>
    /^(raid\d*|md|lvm|crypt)$/i.test(device.type),
  );
  const physicalCount = (topology?.devices ?? []).filter(
    (device) => device.type.toLowerCase() === "disk",
  ).length;
  const raidCount = topologyLayers.filter((device) =>
    /^(raid\d*|md)$/i.test(device.type),
  ).length;
  const lvmCount = topologyLayers.filter(
    (device) => device.type.toLowerCase() === "lvm",
  ).length;
  return (
    <>
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-[24px] font-semibold tracking-tight">存储健康</h1>
          <p className="mt-1 text-[13px] text-slate-500">
            只读显示 OpenMediaVault 管理的存储卷和磁盘健康
          </p>
        </div>
        <button
          type="button"
          onClick={() => setReloadKey((value) => value + 1)}
          disabled={loading}
          className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 text-xs font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:opacity-50"
        >
          <RefreshCwIcon
            className={`size-3.5 ${loading ? "animate-spin" : ""}`}
          />
          刷新
        </button>
      </header>

      <section className="mt-6 rounded-2xl border border-slate-200/90 bg-white p-5 shadow-sm">
        <div className="flex items-center gap-3">
          <span
            className={`grid size-10 place-items-center rounded-xl ${
              status?.available
                ? "bg-emerald-50 text-emerald-600"
                : "bg-amber-50 text-amber-600"
            }`}
          >
            {loading ? (
              <Loader2Icon className="size-5 animate-spin" />
            ) : status?.available ? (
              <CheckCircle2Icon className="size-5" />
            ) : (
              <TriangleAlertIcon className="size-5" />
            )}
          </span>
          <div>
            <h2 className="text-[15px] font-semibold">
              {loading
                ? "正在连接 OMV…"
                : status?.available
                  ? "OMV 只读桥已连接"
                  : status?.configured
                    ? "OMV 只读桥不可用"
                    : "尚未接入 OpenMediaVault"}
            </h2>
            <p className="mt-0.5 text-xs text-slate-500">
              {status?.available
                ? "Echo 只能查看状态，不能格式化磁盘或修改阵列。"
                : "Echo 其他桌面、Agent 和文件功能不受影响。"}
            </p>
          </div>
        </div>
      </section>

      {!loading && healthSnapshot && (
        <section
          aria-label="持续存储监测"
          className={`mt-3 rounded-2xl border p-4 text-xs ${
            healthSnapshot.state === "critical" ||
            healthSnapshot.state === "unavailable"
              ? "border-red-200 bg-red-50 text-red-800"
              : healthSnapshot.state === "warning"
                ? "border-amber-200 bg-amber-50 text-amber-800"
                : "border-emerald-200 bg-emerald-50 text-emerald-800"
          }`}
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <strong className="font-semibold">
                {healthSnapshot.state === "unavailable"
                  ? "持续监测：OMV 连接中断"
                  : healthSnapshot.state === "critical"
                    ? "持续监测发现严重故障"
                    : healthSnapshot.state === "warning"
                      ? "持续监测发现需要关注的状态"
                      : healthSnapshot.state === "pending"
                        ? "持续监测正在启动"
                        : "持续监测未发现异常"}
              </strong>
              <p className="mt-1 leading-5 opacity-80">
                {healthSnapshot.summary.total > 0
                  ? `${healthSnapshot.summary.critical} 项严重 · ${healthSnapshot.summary.warning} 项提醒`
                  : "磁盘 SMART、温度、卷容量和软件阵列状态正常。"}
                {healthSnapshot.stale ? " · 当前数据已过期" : ""}
              </p>
            </div>
            <span className="shrink-0 text-[10px] opacity-70">
              每 {Math.max(1, Math.round(healthSnapshot.intervalSeconds / 60))}{" "}
              分钟
            </span>
          </div>

          {healthSnapshot.activeAlerts.length > 0 && (
            <div className="mt-3 space-y-1.5 border-t border-current/10 pt-3">
              {healthSnapshot.activeAlerts.map((alert) => (
                <article
                  key={alert.id}
                  className="rounded-lg bg-white/70 px-3 py-2"
                >
                  <div className="flex items-start justify-between gap-3">
                    <span className="font-medium">{alert.message}</span>
                    <span className="shrink-0 font-mono text-[10px] opacity-60">
                      {alert.resource}
                    </span>
                  </div>
                  <p className="mt-1 text-[10px] opacity-65">
                    首次 {formatTimestamp(alert.firstSeenAt)} · 最近{" "}
                    {formatTimestamp(alert.lastSeenAt)}
                    {alert.occurrences > 1
                      ? ` · 连续 ${alert.occurrences} 次`
                      : ""}
                  </p>
                </article>
              ))}
            </div>
          )}

          {healthSnapshot.events.length > 0 && (
            <details className="mt-3 border-t border-current/10 pt-3">
              <summary className="cursor-pointer text-[11px] font-medium">
                最近告警变化
              </summary>
              <div className="mt-2 space-y-1 text-[10px] opacity-75">
                {healthSnapshot.events
                  .slice(-5)
                  .reverse()
                  .map((event) => (
                    <p key={event.id}>
                      {event.event === "opened"
                        ? "出现"
                        : event.event === "resolved"
                          ? "恢复"
                          : "变化"}
                      ：{event.message} · {formatTimestamp(event.at)}
                    </p>
                  ))}
              </div>
            </details>
          )}

          {!healthSnapshot.persistenceHealthy && (
            <p role="alert" className="mt-3 font-medium">
              告警状态无法安全写入设备存储；重启后可能无法保留历史。
            </p>
          )}
        </section>
      )}

      {error && (
        <p
          role="alert"
          className="mt-3 rounded-xl bg-red-50 px-4 py-3 text-xs text-red-700"
        >
          {error}
        </p>
      )}

      {!loading && status?.available && filesystems.length === 0 && (
        <p className="mt-4 rounded-2xl border border-slate-200 bg-white p-5 text-sm text-slate-500 shadow-sm">
          OMV 当前没有返回可显示的已挂载数据卷。
        </p>
      )}

      {!loading && status?.available && devices.length > 0 && (
        <section className="mt-4 rounded-2xl border border-slate-200/90 bg-white p-5 shadow-sm">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div>
              <h2 className="text-[15px] font-semibold">物理磁盘</h2>
              <p className="mt-0.5 text-[11px] text-slate-400">
                OMV SMART 枚举 · 已隐藏序列号和 by-id 路径
              </p>
            </div>
            <span className="rounded-full bg-slate-100 px-2 py-1 text-[10px] font-medium text-slate-600">
              {devices.length} 块
            </span>
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {devices.map((device) => {
              const isHealthy = healthy(device.health);
              const isHot = (device.temperatureC ?? 0) >= 50;
              const detail = smart[device.devicefile];
              return (
                <article
                  key={device.devicefile}
                  className={`rounded-xl border p-3 ${
                    !isHealthy || isHot
                      ? "border-amber-200 bg-amber-50/70"
                      : "border-slate-200 bg-slate-50"
                  }`}
                >
                  <div className="flex items-start gap-2.5">
                    <span
                      className={`mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg ${
                        isHealthy && !isHot
                          ? "bg-emerald-100 text-emerald-700"
                          : "bg-amber-100 text-amber-700"
                      }`}
                    >
                      {isHealthy && !isHot ? (
                        <CheckCircle2Icon className="size-4" />
                      ) : (
                        <TriangleAlertIcon className="size-4" />
                      )}
                    </span>
                    <div className="min-w-0 flex-1">
                      <strong className="block truncate text-xs text-slate-800">
                        {device.model || "未知磁盘"}
                      </strong>
                      <span className="mt-0.5 block text-[10px] text-slate-400">
                        {device.devicefile}
                        {device.sizeBytes == null
                          ? ""
                          : ` · ${formatBytes(device.sizeBytes)}`}
                      </span>
                      <div className="mt-2 flex items-center gap-3 text-[11px]">
                        <span
                          className={
                            isHealthy ? "text-emerald-700" : "text-amber-800"
                          }
                        >
                          {device.health}
                        </span>
                        <span
                          className={
                            isHot
                              ? "font-semibold text-red-700"
                              : "text-slate-600"
                          }
                        >
                          {device.temperatureC == null
                            ? "温度未知"
                            : `${device.temperatureC}°C`}
                        </span>
                      </div>
                      {detail ? (
                        <p className="mt-2 text-[10px] text-slate-500">
                          通电 {detail.powerOnHours ?? "未知"} 小时 · 启停{" "}
                          {detail.powerCycles ?? "未知"} 次
                        </p>
                      ) : (
                        <button
                          type="button"
                          disabled={smartLoading === device.devicefile}
                          onClick={() => void readSmart(device.devicefile)}
                          className="mt-2 inline-flex items-center gap-1 text-[10px] font-medium text-blue-600 hover:text-blue-700 disabled:opacity-50"
                        >
                          {smartLoading === device.devicefile && (
                            <Loader2Icon className="size-3 animate-spin" />
                          )}
                          {smartLoading === device.devicefile
                            ? "正在读取…"
                            : "读取通电详情"}
                        </button>
                      )}
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      )}

      {!loading && status?.available && topology && (
        <section className="mt-4 rounded-2xl border border-slate-200/90 bg-white p-5 shadow-sm">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-[15px] font-semibold">存储拓扑</h2>
              <p className="mt-0.5 text-[11px] text-slate-400">
                物理盘、软件 RAID 与 LVM 的只读关系
              </p>
            </div>
            <Layers3Icon className="size-5 text-blue-500" />
          </div>
          <div className="mt-3 flex flex-wrap gap-2 text-[10px] font-medium">
            <span className="rounded-full bg-slate-100 px-2.5 py-1 text-slate-600">
              物理盘 {physicalCount}
            </span>
            <span className="rounded-full bg-blue-50 px-2.5 py-1 text-blue-700">
              阵列 {raidCount}
            </span>
            <span className="rounded-full bg-violet-50 px-2.5 py-1 text-violet-700">
              LVM {lvmCount}
            </span>
          </div>

          {topologyLayers.length === 0 ? (
            <p className="mt-3 rounded-xl bg-slate-50 px-3 py-3 text-xs text-slate-500">
              当前是直连磁盘或分区，没有发现软件 RAID / LVM 层。
            </p>
          ) : (
            <div className="mt-3 space-y-2">
              {topologyLayers.map((node) => {
                const array = topology.arrays.find(
                  (candidate) => candidate.devicefile === node.devicefile,
                );
                const arrayHealthy = array?.status === "healthy";
                return (
                  <article
                    key={node.devicefile}
                    className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-[11px]"
                  >
                    <span className="min-w-0 text-slate-500">
                      {node.parentDevicefiles.length > 0
                        ? node.parentDevicefiles.join(" + ")
                        : "未识别上游设备"}
                    </span>
                    <ArrowRightIcon className="size-3.5 shrink-0 text-slate-400" />
                    <strong className="text-slate-800">
                      {node.devicefile}
                    </strong>
                    <span className="rounded bg-white px-1.5 py-0.5 font-medium text-slate-600">
                      {topologyKind(node.type)}
                    </span>
                    {array && (
                      <span
                        className={`ml-auto rounded-full px-2 py-0.5 font-medium ${
                          arrayHealthy
                            ? "bg-emerald-100 text-emerald-700"
                            : "bg-amber-100 text-amber-800"
                        }`}
                      >
                        {array.level.toUpperCase()} · {raidStatus(array.status)}
                        {array.totalDevices == null
                          ? ""
                          : ` ${array.activeDevices}/${array.totalDevices}`}
                        {array.operationPercent == null
                          ? ""
                          : ` · ${array.operationPercent}%`}
                      </span>
                    )}
                  </article>
                );
              })}
            </div>
          )}
        </section>
      )}

      <div className="mt-4 space-y-3">
        {filesystems.map((filesystem) => {
          const device = filesystem.parentdevicefile || filesystem.devicefile;
          const report = smart[device];
          const canReadSmart = devices.some(
            (physicalDevice) => physicalDevice.devicefile === device,
          );
          const used =
            filesystem.usedPercent ??
            (filesystem.sizeBytes > 0
              ? Math.round(
                  ((filesystem.sizeBytes - filesystem.availableBytes) /
                    filesystem.sizeBytes) *
                    100,
                )
              : 0);
          return (
            <article
              key={`${filesystem.devicefile}:${filesystem.mountpoint}`}
              className="rounded-2xl border border-slate-200/90 bg-white p-5 shadow-sm"
            >
              <div className="flex items-start gap-4">
                <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-blue-50 text-blue-600">
                  <HardDriveIcon className="size-5" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h2 className="truncate text-[15px] font-semibold">
                        {filesystem.label || filesystem.devicefile}
                      </h2>
                      <p className="mt-0.5 truncate text-[11px] text-slate-400">
                        {filesystem.type.toUpperCase()} ·{" "}
                        {filesystem.mountpoint}
                      </p>
                    </div>
                    {filesystem.readOnly && (
                      <span className="rounded-full bg-amber-50 px-2 py-1 text-[10px] font-medium text-amber-700">
                        只读挂载
                      </span>
                    )}
                  </div>
                  <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100">
                    <div
                      className={`h-full rounded-full ${
                        used >= 90
                          ? "bg-red-500"
                          : used >= 75
                            ? "bg-amber-500"
                            : "bg-blue-500"
                      }`}
                      style={{ width: `${Math.max(0, Math.min(100, used))}%` }}
                    />
                  </div>
                  <div className="mt-1.5 flex justify-between text-[11px] text-slate-500">
                    <span>已使用 {used}%</span>
                    <span>
                      可用 {formatBytes(filesystem.availableBytes)} /{" "}
                      {formatBytes(filesystem.sizeBytes)}
                    </span>
                  </div>

                  {report ? (
                    <div className="mt-4 grid grid-cols-3 gap-2 rounded-xl bg-slate-50 p-3 text-xs">
                      <div>
                        <span className="block text-[10px] text-slate-400">
                          SMART
                        </span>
                        <strong
                          className={
                            healthy(report.health)
                              ? "text-emerald-600"
                              : "text-amber-700"
                          }
                        >
                          {report.health}
                        </strong>
                      </div>
                      <div>
                        <span className="block text-[10px] text-slate-400">
                          温度
                        </span>
                        <strong className="inline-flex items-center gap-1 text-slate-700">
                          <ThermometerIcon className="size-3" />
                          {report.temperatureC == null
                            ? "未知"
                            : `${report.temperatureC}°C`}
                        </strong>
                      </div>
                      <div>
                        <span className="block text-[10px] text-slate-400">
                          通电时间
                        </span>
                        <strong className="text-slate-700">
                          {report.powerOnHours == null
                            ? "未知"
                            : `${report.powerOnHours} 小时`}
                        </strong>
                      </div>
                      {report.model && (
                        <p className="col-span-3 truncate border-t border-slate-200 pt-2 text-[10px] text-slate-500">
                          {report.model}
                        </p>
                      )}
                    </div>
                  ) : canReadSmart ? (
                    <button
                      type="button"
                      disabled={smartLoading === device}
                      onClick={() => void readSmart(device)}
                      className="mt-3 inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 px-3 text-[11px] font-medium text-slate-700 transition hover:bg-slate-100 disabled:opacity-50"
                    >
                      {smartLoading === device ? (
                        <Loader2Icon className="size-3.5 animate-spin" />
                      ) : (
                        <ShieldCheckIcon className="size-3.5" />
                      )}
                      {smartLoading === device ? "正在读取…" : "查看 SMART"}
                    </button>
                  ) : (
                    <p className="mt-3 text-[10px] text-slate-400">
                      该卷位于阵列或逻辑层；SMART 请查看上方对应物理磁盘。
                    </p>
                  )}
                </div>
              </div>
            </article>
          );
        })}
      </div>

      <p className="mt-4 text-[11px] leading-5 text-slate-400">
        Echo 会在后台持续检测并保留最近告警变化；如需邮件或推送，请同时配置 OMV
        官方通知。序列号和原始 SMART 文本不会进入
        Echo；阵列修复、格式化、共享和权限修改 仍请在 OMV 中完成。
      </p>
    </>
  );
}
