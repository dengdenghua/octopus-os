import { useEffect, useRef, useState } from "react";
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
  fetchNativeFilesystems,
  fetchNativeHealth,
  fetchNativeSmart,
  fetchNativeSmartDevices,
  fetchNativeStatus,
  fetchNativeStorageTopology,
  type OmvFilesystem,
  type OmvHealthSnapshot,
  type OmvSmart,
  type OmvSmartDevice,
  type OmvStorageTopology,
  type OmvStatus,
  type StorageProbeEvidence,
} from "@/appliance/omv";
import { SmartSelfTestControls } from "@/appliance/smart-self-test-controls";

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

function healthy(value: string | null | undefined) {
  return /^(passed|ok|good|healthy|true)$/i.test((value ?? "").trim());
}

function unknownHealth(value: string | null | undefined) {
  return (
    !value?.trim() ||
    /^(unknown|unavailable|unsupported|n\/a)$/i.test(value.trim())
  );
}

function smartUnconfirmed(report: OmvSmart | OmvSmartDevice) {
  return (
    unknownHealth(report.health) ||
    (healthy(report.health) &&
      (report.available === false ||
        report.coverage === "none" ||
        report.coverage === "partial" ||
        report.probeEvidence?.some(
          (probe) => !["ok", "not-applicable"].includes(probe.state),
        )))
  );
}

function probeGuidance(probe: StorageProbeEvidence) {
  if (probe.source === "smart") {
    if (probe.code === "tool_missing")
      return "SMART 组件尚未安装，健康状态未确认。请设备管理员安装组件后刷新。";
    if (probe.code === "unsupported")
      return "该磁盘未提供可用的 SMART 健康数据，请检查磁盘或转接设备是否支持。";
    return "未取得完整 SMART 结果。请设备管理员检查 SMART 组件、磁盘支持和读取权限。";
  }
  if (probe.source === "block-devices") {
    return probe.state === "empty"
      ? "未发现可检查的物理磁盘。请确认磁盘连接或虚拟机磁盘映射后刷新。"
      : "磁盘枚举未完成。请设备管理员检查设备访问权限后刷新。";
  }
  if (probe.source === "filesystems") {
    return "卷容量读取未完成。请检查挂载状态和读取权限后刷新。";
  }
  return "阵列检查未完成。请设备管理员检查阵列组件和读取权限后刷新。";
}

function usedPercent(filesystem: OmvFilesystem): number | null {
  if (!Number.isFinite(filesystem.sizeBytes) || filesystem.sizeBytes <= 0)
    return null;
  if (
    Number.isFinite(filesystem.usedPercent) &&
    filesystem.usedPercent != null &&
    filesystem.usedPercent >= 0 &&
    filesystem.usedPercent <= 100
  )
    return filesystem.usedPercent;
  if (
    Number.isFinite(filesystem.sizeBytes) &&
    filesystem.sizeBytes > 0 &&
    Number.isFinite(filesystem.availableBytes) &&
    filesystem.availableBytes >= 0 &&
    filesystem.availableBytes <= filesystem.sizeBytes
  ) {
    return Math.round(
      (1 - filesystem.availableBytes / filesystem.sizeBytes) * 100,
    );
  }
  return null;
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
  const [readErrors, setReadErrors] = useState<string[]>([]);
  const generation = useRef(0);

  useEffect(() => {
    let alive = true;
    generation.current += 1;
    setLoading(true);
    setError(null);
    setReadErrors([]);
    setStatus(null);
    setFilesystems([]);
    setDevices([]);
    setTopology(null);
    setSmart({});
    setSmartLoading(null);
    setHealthSnapshot(null);
    // 存储面完全原生化:数据权威是主机本身(内核 / zpool / smartctl)。
    Promise.allSettled([
      fetchNativeStatus(),
      fetchNativeHealth(),
      fetchNativeFilesystems(),
      fetchNativeSmartDevices(),
      fetchNativeStorageTopology(),
    ]).then(
      ([nextStatus, nextHealth, volumes, physicalDevices, storageTopology]) => {
        if (!alive) return;
        if (nextStatus.status === "fulfilled") setStatus(nextStatus.value);
        if (nextHealth.status === "fulfilled")
          setHealthSnapshot(nextHealth.value);
        if (volumes.status === "fulfilled") setFilesystems(volumes.value);
        if (physicalDevices.status === "fulfilled")
          setDevices(physicalDevices.value);
        if (storageTopology.status === "fulfilled")
          setTopology(storageTopology.value);
        setReadErrors(
          [
            ["存储连接", nextStatus],
            ["健康检查", nextHealth],
            ["卷容量", volumes],
            ["物理磁盘 SMART", physicalDevices],
            ["存储拓扑", storageTopology],
          ].flatMap(([label, result]) => {
            const outcome = result as PromiseSettledResult<unknown>;
            return outcome.status === "rejected"
              ? [
                  `${label}读取失败：${outcome.reason instanceof Error ? outcome.reason.message : "请检查设备连接与读取权限后刷新"}`,
                ]
              : [];
          }),
        );
        setLoading(false);
      },
    );
    return () => {
      alive = false;
      generation.current += 1;
    };
  }, [reloadKey]);

  const readSmart = async (device: string) => {
    const requestGeneration = generation.current;
    setSmartLoading(device);
    setError(null);
    try {
      const report = await fetchNativeSmart(device);
      if (requestGeneration !== generation.current) return;
      setSmart((current) => ({ ...current, [device]: report }));
    } catch (reason) {
      if (requestGeneration !== generation.current) return;
      setError(
        reason instanceof Error ? reason.message : "无法读取 SMART 状态",
      );
    } finally {
      if (requestGeneration === generation.current) {
        setSmartLoading((current) => (current === device ? null : current));
      }
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
  const evidence = healthSnapshot?.probeEvidence ?? [];
  const incompleteProbes = evidence.filter(
    (probe) =>
      probe.required &&
      (["partial", "unavailable", "error"].includes(probe.state) ||
        (probe.source === "block-devices" && probe.state === "empty")),
  );
  const hasDevices =
    devices.length > 0 ||
    physicalCount > 0 ||
    evidence.some(
      (probe) =>
        probe.source === "block-devices" &&
        probe.state === "ok" &&
        (probe.count ?? 0) > 0,
    );
  const critical =
    healthSnapshot?.state === "critical" ||
    healthSnapshot?.activeAlerts.some(
      (alert) => alert.severity === "critical",
    ) ||
    (healthSnapshot?.summary.critical ?? 0) > 0;
  const warning =
    healthSnapshot?.state === "warning" ||
    healthSnapshot?.activeAlerts.some(
      (alert) => alert.severity === "warning",
    ) ||
    (healthSnapshot?.summary.warning ?? 0) > 0;
  const confirmedHealthy =
    healthSnapshot?.state === "healthy" &&
    healthSnapshot.stale === false &&
    healthSnapshot.checkedAt != null &&
    Number.isFinite(Date.parse(healthSnapshot.checkedAt)) &&
    healthSnapshot.coverage === "complete" &&
    evidence.length > 0 &&
    incompleteProbes.length === 0 &&
    hasDevices &&
    readErrors.length === 0 &&
    devices.every(
      (device) =>
        healthy(device.health) &&
        !smartUnconfirmed(device) &&
        (device.temperatureC ?? 0) < 50,
    );
  const monitorLabel = healthSnapshot?.monitoring ? "持续监测" : "本次检查";
  const emptyInventory = (status?.probeEvidence ?? evidence).some(
    (probe) => probe.source === "block-devices" && probe.state === "empty",
  );
  return (
    <>
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-[24px] font-semibold tracking-tight">存储健康</h1>
          <p className="mt-1 text-[13px] text-slate-500">
            只读显示本机存储卷、磁盘健康与阵列状态
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
                ? "bg-blue-50 text-blue-600"
                : "bg-amber-50 text-amber-600"
            }`}
          >
            {loading ? (
              <Loader2Icon className="size-5 animate-spin" />
            ) : status?.available ? (
              <HardDriveIcon className="size-5" />
            ) : (
              <TriangleAlertIcon className="size-5" />
            )}
          </span>
          <div>
            <h2 className="text-[15px] font-semibold">
              {loading
                ? "正在读取存储状态…"
                : status?.available
                  ? "原生存储面已连接"
                  : emptyInventory
                    ? "未检测到存储设备"
                    : "存储连接尚未确认"}
            </h2>
            <p className="mt-0.5 text-xs text-slate-500">
              {status?.available
                ? "此页为只读检查，连接成功不代表磁盘健康状态已确认。"
                : "请检查磁盘连接、设备映射与读取权限后刷新。下方保留已取得的检查结果。"}
            </p>
          </div>
        </div>
      </section>

      {!loading && (
        <section
          aria-label="存储健康检查"
          className={`mt-3 rounded-2xl border p-4 text-xs ${
            critical
              ? "border-red-200 bg-red-50 text-red-800"
              : warning || healthSnapshot?.state === "degraded"
                ? "border-amber-200 bg-amber-50 text-amber-800"
                : confirmedHealthy
                  ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                  : "border-slate-200 bg-slate-50 text-slate-700"
          }`}
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <strong className="font-semibold">
                {critical
                  ? `${monitorLabel}发现严重故障`
                  : warning
                    ? `${monitorLabel}发现需要关注的状态`
                    : confirmedHealthy
                      ? `${monitorLabel}已完成，已检查项目未发现异常`
                      : healthSnapshot?.stale
                        ? "检查结果不完整或已过期"
                        : healthSnapshot?.state === "pending"
                          ? "健康检查正在启动"
                          : "存储健康尚未确认"}
              </strong>
              <p className="mt-1 leading-5 opacity-80">
                {healthSnapshot && healthSnapshot.summary.total > 0
                  ? `${healthSnapshot.summary.critical} 项严重 · ${healthSnapshot.summary.warning} 项提醒。`
                  : confirmedHealthy
                    ? "本次已取得所需检查结果。"
                    : "尚无足够检查结果判断整体健康；请查看未完成项目并刷新。"}
                {healthSnapshot?.stale
                  ? " 当前数据不完整或已过期，已知告警仍需处理。"
                  : ""}
                {healthSnapshot?.checkedAt
                  ? ` 检查时间：${formatTimestamp(healthSnapshot.checkedAt)}`
                  : ""}
              </p>
            </div>
            <span className="shrink-0 text-[10px] opacity-70">
              {healthSnapshot?.monitoring && healthSnapshot.intervalSeconds > 0
                ? `每 ${Math.max(1, Math.round(healthSnapshot.intervalSeconds / 60))} 分钟`
                : "按需检查"}
            </span>
          </div>

          {incompleteProbes.length > 0 && (
            <ul className="mt-3 space-y-1 border-t border-current/10 pt-3">
              {incompleteProbes.map((probe, index) => (
                <li key={`${probe.source}:${probe.target ?? ""}:${index}`}>
                  {probe.target ? `${probe.target}：` : ""}
                  {probeGuidance(probe)}
                </li>
              ))}
            </ul>
          )}

          {healthSnapshot && healthSnapshot.activeAlerts.length > 0 && (
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

          {healthSnapshot && healthSnapshot.events.length > 0 && (
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

          {healthSnapshot?.persistenceHealthy === false && (
            <p role="alert" className="mt-3 font-medium">
              告警状态无法安全写入设备存储；重启后可能无法保留历史。
            </p>
          )}
        </section>
      )}

      {readErrors.length > 0 && (
        <div
          role="alert"
          className="mt-3 rounded-xl bg-amber-50 px-4 py-3 text-xs text-amber-800"
        >
          {readErrors.map((message) => (
            <p key={message}>{message}</p>
          ))}
          <p className="mt-1">
            已成功读取的卷和磁盘仍显示在下方。请检查对应组件或读取权限后刷新。
          </p>
        </div>
      )}

      {error && (
        <p
          role="alert"
          className="mt-3 rounded-xl bg-red-50 px-4 py-3 text-xs text-red-700"
        >
          {error}
        </p>
      )}

      {!loading && filesystems.length === 0 && (
        <p className="mt-4 rounded-2xl border border-slate-200 bg-white p-5 text-sm text-slate-500 shadow-sm">
          当前没有可显示的已挂载数据卷；请结合上方检查结果确认挂载状态。
        </p>
      )}

      {!loading && devices.length > 0 && (
        <section className="mt-4 rounded-2xl border border-slate-200/90 bg-white p-5 shadow-sm">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div>
              <h2 className="text-[15px] font-semibold">物理磁盘</h2>
              <p className="mt-0.5 text-[11px] text-slate-400">
                原生 SMART 枚举 · 已隐藏序列号和 by-id 路径
              </p>
            </div>
            <span className="rounded-full bg-slate-100 px-2 py-1 text-[10px] font-medium text-slate-600">
              {devices.length} 块
            </span>
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {devices.map((device) => {
              const detail = smart[device.devicefile];
              const currentReport = detail ?? device;
              const probe =
                currentReport.probeEvidence ??
                evidence.filter(
                  (entry) =>
                    entry.source === "smart" &&
                    entry.target === device.devicefile,
                );
              const isUnknown = smartUnconfirmed({
                ...currentReport,
                probeEvidence: probe,
              });
              const isHealthy = healthy(currentReport.health) && !isUnknown;
              const isHot = (currentReport.temperatureC ?? 0) >= 50;
              return (
                <article
                  key={device.devicefile}
                  className={`rounded-xl border p-3 ${
                    (!isHealthy && !isUnknown) || isHot
                      ? "border-amber-200 bg-amber-50/70"
                      : "border-slate-200 bg-slate-50"
                  }`}
                >
                  <div className="flex items-start gap-2.5">
                    <span
                      className={`mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg ${
                        isHealthy && !isHot
                          ? "bg-emerald-100 text-emerald-700"
                          : isUnknown && !isHot
                            ? "bg-slate-200 text-slate-600"
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
                            isHealthy
                              ? "text-emerald-700"
                              : isUnknown
                                ? "text-slate-600"
                                : "text-amber-800"
                          }
                        >
                          {isUnknown ? "健康状态未知" : currentReport.health}
                        </span>
                        <span
                          className={
                            isHot
                              ? "font-semibold text-red-700"
                              : "text-slate-600"
                          }
                        >
                          {currentReport.temperatureC == null
                            ? "温度未知"
                            : `${currentReport.temperatureC}°C`}
                        </span>
                      </div>
                      {isUnknown && (
                        <p className="mt-2 text-[10px] text-slate-500">
                          未取得完整健康结论，请检查 SMART
                          组件、磁盘支持与读取权限。
                        </p>
                      )}
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
                      {detail &&
                        status?.capabilities.includes(
                          "storage.smart.self-test.start.v1",
                        ) && (
                          <SmartSelfTestControls
                            devicefile={device.devicefile}
                          />
                        )}
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      )}

      {!loading && topology && (
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
              {topology.devices.length > 0
                ? "当前已读取的拓扑中没有软件 RAID / LVM 层。"
                : "尚未取得设备拓扑，请检查磁盘枚举结果后刷新。"}
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
          const reportUnknown = report && smartUnconfirmed(report);
          const canReadSmart = devices.some(
            (physicalDevice) => physicalDevice.devicefile === device,
          );
          const used = usedPercent(filesystem);
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
                        used != null && used >= 90
                          ? "bg-red-500"
                          : used != null && used >= 75
                            ? "bg-amber-500"
                            : "bg-blue-500"
                      }`}
                      style={{
                        width:
                          used == null
                            ? "0%"
                            : `${Math.max(0, Math.min(100, used))}%`,
                      }}
                    />
                  </div>
                  <div className="mt-1.5 flex justify-between text-[11px] text-slate-500">
                    <span>
                      {used == null ? "容量使用率未知" : `已使用 ${used}%`}
                    </span>
                    <span>
                      {filesystem.sizeBytes > 0
                        ? `可用 ${formatBytes(filesystem.availableBytes)} / ${formatBytes(filesystem.sizeBytes)}`
                        : "尚未取得容量，请检查挂载状态后刷新"}
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
                            healthy(report.health) && !reportUnknown
                              ? "text-emerald-600"
                              : reportUnknown
                                ? "text-slate-600"
                                : "text-amber-700"
                          }
                        >
                          {reportUnknown ? "健康状态未知" : report.health}
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
                      尚未关联到可读取 SMART 的物理磁盘；请检查设备拓扑、SMART
                      组件与读取权限。
                    </p>
                  )}
                </div>
              </div>
            </article>
          );
        })}
      </div>

      <p className="mt-4 text-[11px] leading-5 text-slate-400">
        {healthSnapshot?.monitoring
          ? "后台监测已启用，最近告警变化显示在上方。"
          : "当前为按需检查；点击“刷新”重新读取，尚未启用后台持续监测。"}
        序列号和原始 SMART 文本不会进入 Echo。
      </p>
    </>
  );
}
