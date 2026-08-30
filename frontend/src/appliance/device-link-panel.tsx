import { useEffect, useMemo, useState } from "react";
import {
  BatteryChargingIcon,
  BatteryMediumIcon,
  CheckIcon,
  ClipboardIcon,
  ExternalLinkIcon,
  FileIcon,
  Globe2Icon,
  ImagesIcon,
  LaptopIcon,
  Link2Icon,
  Loader2Icon,
  LockKeyholeIcon,
  PlusIcon,
  RefreshCwIcon,
  RouterIcon,
  ShieldCheckIcon,
  SmartphoneIcon,
  Trash2Icon,
  UnplugIcon,
  UploadCloudIcon,
  WifiIcon,
} from "lucide-react";

import { requestHighRiskApproval } from "@/appliance/approval";
import {
  createPairingInvitation,
  disableDeviceLink,
  enableDeviceLink,
  fetchDeviceLinkStatus,
  revokeLinkedDevice,
  type DeviceLinkStatus,
  type LinkedDevice,
  type PairingInvitation,
} from "@/appliance/device-link";
import {
  fetchDeviceSyncStatus,
  setDeviceSyncScope,
  type DeviceSyncDevice,
  type DeviceSyncScope,
  type DeviceSyncStatus,
} from "@/appliance/device-sync";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";
import { cn } from "@/lib/utils";

type DeviceLinkSection = "devices" | "pair" | "sync" | "remote";
type PendingAction =
  | { kind: "enable" }
  | { kind: "disable" }
  | { kind: "pair" }
  | {
      kind: "sync";
      device: DeviceSyncDevice;
      scope: DeviceSyncScope;
      enabled: boolean;
    }
  | { kind: "revoke"; device: LinkedDevice };

function timeLabel(value: number | null) {
  if (!value) return "尚无记录";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value * 1_000));
}

function deviceName(device: LinkedDevice) {
  return [device.brand, device.model].filter(Boolean).join(" ") || device.id;
}

function bytesLabel(value: number) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const order = Math.min(
    Math.floor(Math.log(value) / Math.log(1024)),
    units.length - 1,
  );
  return `${(value / 1024 ** order).toFixed(order > 1 ? 1 : 0)} ${units[order]}`;
}

function DeviceCard({
  device,
  onRevoke,
}: {
  device: LinkedDevice;
  onRevoke: () => void;
}) {
  const DeviceIcon = device.type === "desktop" ? LaptopIcon : SmartphoneIcon;
  return (
    <article className="flex items-center gap-4 rounded-[20px] border border-white/90 bg-white/78 p-4 shadow-[0_10px_30px_rgba(51,65,85,.07)]">
      <span className="relative grid size-12 shrink-0 place-items-center rounded-[16px] bg-gradient-to-br from-blue-500 to-indigo-600 text-white shadow-sm">
        <DeviceIcon className="size-6" />
        <i
          aria-label={device.online ? "在线" : "离线"}
          className={cn(
            "absolute -bottom-0.5 -right-0.5 size-3 rounded-full border-2 border-white",
            device.online ? "bg-emerald-500" : "bg-slate-300",
          )}
        />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <strong className="truncate text-[14px] text-slate-900">
            {deviceName(device)}
          </strong>
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-[9px] font-semibold",
              device.online
                ? "bg-emerald-50 text-emerald-700"
                : "bg-slate-100 text-slate-500",
            )}
          >
            {device.online ? (device.busy ? "使用中" : "在线") : "离线"}
          </span>
        </div>
        <p className="mt-1 truncate text-[10px] text-slate-400">
          {device.platform} · {device.id} · 最近连接{" "}
          {timeLabel(device.lastSeenAt)}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {device.battery !== null && (
            <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-1 text-[9px] text-slate-500">
              {device.charging ? (
                <BatteryChargingIcon className="size-3" />
              ) : (
                <BatteryMediumIcon className="size-3" />
              )}
              {device.battery}%
            </span>
          )}
          {device.totalCapabilities > 0 && (
            <span className="rounded-full bg-blue-50 px-2 py-1 text-[9px] text-blue-700">
              {device.totalCapabilities} 项设备能力
            </span>
          )}
          {device.currentApp && (
            <span className="max-w-40 truncate rounded-full bg-violet-50 px-2 py-1 text-[9px] text-violet-700">
              {device.currentApp}
            </span>
          )}
        </div>
      </div>
      {device.individuallyRevocable && (
        <button
          type="button"
          aria-label={`移除 ${deviceName(device)}`}
          title="移除设备"
          onClick={onRevoke}
          className="grid size-8 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-red-50 hover:text-red-600"
        >
          <Trash2Icon className="size-4" />
        </button>
      )}
    </article>
  );
}

export function DeviceLinkPanel({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [section, setSection] = useState<DeviceLinkSection>("devices");
  const [status, setStatus] = useState<DeviceLinkStatus | null>(null);
  const [syncStatus, setSyncStatus] = useState<DeviceSyncStatus | null>(null);
  const [invitation, setInvitation] = useState<PairingInvitation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(
    null,
  );
  const [copied, setCopied] = useState(false);

  const refresh = () => {
    setLoading(true);
    setError(null);
    return Promise.all([
      fetchDeviceLinkStatus(),
      fetchDeviceSyncStatus().catch(() => null),
    ])
      .then(([nextStatus, nextSyncStatus]) => {
        setStatus(nextStatus);
        setSyncStatus(nextSyncStatus);
      })
      .catch((reason) => {
        setError(
          reason instanceof Error ? reason.message : "无法读取设备连接状态",
        );
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (!open) return;
    setSection("devices");
    setInvitation(null);
    setCopied(false);
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5_000);
    return () => window.clearInterval(timer);
  }, [open]);

  const approvalCopy = useMemo(() => {
    if (!pendingAction) return null;
    if (pendingAction.kind === "enable") {
      return {
        title: "开启局域网设备连接",
        description:
          "Echo 将在局域网开放一个经过配对凭据保护的设备入口，用于连接你的手机或其他终端。",
        target: "仅同一局域网 · 端口 8765",
        confirm: "确认开启",
        destructive: false,
      };
    }
    if (pendingAction.kind === "disable") {
      return {
        title: "关闭设备连接",
        description: "所有当前设备连接会断开，之后需要重新开启才能连接。",
        target: "局域网设备入口",
        confirm: "关闭连接",
        destructive: true,
      };
    }
    if (pendingAction.kind === "pair") {
      return {
        title: "创建配对邀请",
        description:
          "邀请包含设备访问凭据，只应复制到你本人持有的 Echo Mobile。",
        target: "一台新设备",
        confirm: "创建邀请",
        destructive: false,
      };
    }
    if (pendingAction.kind === "sync") {
      const scopeName = pendingAction.scope === "photos" ? "照片" : "文件";
      return {
        title: `${pendingAction.enabled ? "开启" : "关闭"}${scopeName}自动备份`,
        description: pendingAction.enabled
          ? `允许 ${pendingAction.device.name} 使用自己的设备凭据，将${scopeName}写入专属目录。`
          : `停止这台设备的${scopeName}备份；已安全落盘的内容不会删除。`,
        target: pendingAction.device.name,
        confirm: pendingAction.enabled ? "确认开启" : "停止备份",
        destructive: !pendingAction.enabled,
      };
    }
    return {
      title: "移除已配对设备",
      description: "该设备的凭据将立即失效，在线连接也会断开。",
      target: deviceName(pendingAction.device),
      confirm: "移除设备",
      destructive: true,
    };
  }, [pendingAction]);

  const runApprovedAction = async (password: string) => {
    if (!pendingAction) return;
    if (pendingAction.kind === "enable") {
      const approval = await requestHighRiskApproval(
        "device-link.enable",
        "lan",
        password,
      );
      setStatus(await enableDeviceLink(approval.approvalToken));
      setPendingAction(null);
      return;
    }
    if (pendingAction.kind === "disable") {
      const approval = await requestHighRiskApproval(
        "device-link.disable",
        "lan",
        password,
      );
      setStatus(await disableDeviceLink(approval.approvalToken));
      setInvitation(null);
      setPendingAction(null);
      return;
    }
    if (pendingAction.kind === "pair") {
      const approval = await requestHighRiskApproval(
        "device-link.pair",
        "lan",
        password,
      );
      setInvitation(await createPairingInvitation(approval.approvalToken));
      setSection("pair");
      setPendingAction(null);
      return;
    }
    if (pendingAction.kind === "sync") {
      const { device, scope, enabled } = pendingAction;
      const approval = await requestHighRiskApproval(
        `device-sync.${scope}.${enabled ? "enable" : "disable"}`,
        device.id,
        password,
      );
      setSyncStatus(
        await setDeviceSyncScope(
          device.id,
          scope,
          enabled,
          approval.approvalToken,
        ),
      );
      setPendingAction(null);
      return;
    }
    const deviceId = pendingAction.device.id;
    const approval = await requestHighRiskApproval(
      "device-link.device.revoke",
      deviceId,
      password,
    );
    setStatus(await revokeLinkedDevice(deviceId, approval.approvalToken));
    setPendingAction(null);
  };

  if (!open) return null;

  const managed = status?.mode === "echo-managed";
  const active = !!status?.enabled && !!status?.listenerActive;
  const remote = status?.remoteAccess;
  const remoteConnected = !!remote?.available;

  return (
    <div
      className="absolute inset-0 z-[82] grid place-items-center bg-slate-950/22 px-5 pb-20 pt-10 backdrop-blur-[5px]"
      data-desktop-interactive
    >
      <button
        type="button"
        aria-label="关闭设备连接"
        className="absolute inset-0 cursor-default"
        onClick={onClose}
      />
      <section
        role="dialog"
        aria-modal="true"
        aria-label="设备连接"
        className="relative flex h-[min(620px,calc(100vh-120px))] w-[min(920px,calc(100vw-34px))] overflow-hidden rounded-[24px] border border-white/75 bg-[#edf1f6]/94 text-slate-900 shadow-[0_34px_100px_rgba(15,23,42,.38)] backdrop-blur-3xl"
      >
        <aside className="w-56 shrink-0 border-r border-white/70 bg-white/44 px-3 py-4">
          <div className="mb-5 flex items-center justify-between px-1">
            <div className="flex gap-2">
              <button
                type="button"
                aria-label="关闭"
                onClick={onClose}
                className="size-3 rounded-full bg-[#ff5f57] ring-1 ring-black/10"
              />
              <span className="size-3 rounded-full bg-[#febc2e] ring-1 ring-black/10" />
              <span className="size-3 rounded-full bg-[#28c840] ring-1 ring-black/10" />
            </div>
          </div>
          <div className="mb-4 flex items-center gap-3 rounded-2xl bg-white/72 p-3 ring-1 ring-white">
            <span className="grid size-10 place-items-center rounded-[14px] bg-gradient-to-br from-blue-500 to-indigo-600 text-white shadow-sm">
              <Link2Icon className="size-5" />
            </span>
            <span className="min-w-0">
              <strong className="block text-[13px]">设备连接</strong>
              <small className="mt-0.5 block text-[10px] text-slate-400">
                {active
                  ? `${status?.onlineDeviceCount ?? 0} 台在线`
                  : "当前未开启"}
              </small>
            </span>
          </div>
          {(
            [
              ["devices", SmartphoneIcon, "我的设备"],
              ["pair", PlusIcon, "添加设备"],
              ["sync", UploadCloudIcon, "自动备份"],
              ["remote", Globe2Icon, "远程访问"],
            ] as const
          ).map(([id, Icon, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setSection(id)}
              className={cn(
                "mt-1 flex h-9 w-full items-center gap-2 rounded-xl px-3 text-left text-[12px] font-medium transition",
                section === id
                  ? "bg-blue-600 text-white shadow-sm"
                  : "text-slate-600 hover:bg-white/70",
              )}
            >
              <Icon className="size-4" />
              {label}
            </button>
          ))}
          <div className="absolute bottom-4 left-3 right-3 rounded-xl bg-white/45 px-3 py-2.5 text-[9px] leading-4 text-slate-400 ring-1 ring-white/70">
            <span className="flex items-center gap-1.5 font-semibold text-slate-500">
              <ShieldCheckIcon className="size-3.5 text-emerald-500" />
              配对凭据受保护
            </span>
            状态页不会显示设备密钥；新增、关闭和移除均需管理员复核。
          </div>
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto px-7 py-6">
          <header className="flex items-start justify-between gap-4">
            <div>
              <h1 className="text-[24px] font-semibold tracking-tight">
                {section === "devices"
                  ? "我的设备"
                  : section === "pair"
                    ? "添加设备"
                    : section === "sync"
                      ? "自动备份"
                      : "远程访问"}
              </h1>
              <p className="mt-1 text-[11px] text-slate-500">
                {section === "remote"
                  ? "查看公网连接能力与当前安全边界"
                  : section === "sync"
                    ? "按设备分别授权照片与文件，断点续传且不覆盖冲突版本"
                    : "复用 Agent Tentacle，连接手机与其他个人终端"}
              </p>
            </div>
            <button
              type="button"
              aria-label="刷新设备连接状态"
              disabled={loading}
              onClick={() => void refresh()}
              className="grid size-9 place-items-center rounded-full bg-white/72 text-slate-500 shadow-sm ring-1 ring-white transition hover:bg-white disabled:opacity-50"
            >
              <RefreshCwIcon
                className={cn("size-4", loading && "animate-spin")}
              />
            </button>
          </header>

          {error && (
            <p
              role="alert"
              className="mt-4 rounded-xl bg-red-50 px-3 py-2.5 text-xs text-red-700 ring-1 ring-red-100"
            >
              {error}
            </p>
          )}

          {loading && !status ? (
            <div className="grid min-h-72 place-items-center text-sm text-slate-400">
              <span className="flex items-center gap-2">
                <Loader2Icon className="size-5 animate-spin" />{" "}
                正在读取设备连接…
              </span>
            </div>
          ) : section === "devices" ? (
            <>
              <section className="mt-5 flex flex-wrap items-center justify-between gap-3 rounded-[20px] bg-gradient-to-br from-slate-900 via-slate-800 to-blue-950 p-5 text-white shadow-[0_18px_50px_rgba(15,23,42,.2)]">
                <div className="flex items-center gap-3">
                  <span
                    className={cn(
                      "grid size-11 place-items-center rounded-[15px]",
                      active ? "bg-emerald-400/16" : "bg-white/10",
                    )}
                  >
                    {active ? (
                      <WifiIcon className="size-5 text-emerald-300" />
                    ) : (
                      <UnplugIcon className="size-5 text-white/55" />
                    )}
                  </span>
                  <div>
                    <strong className="block text-[15px]">
                      {active ? "局域网设备入口已开启" : "设备连接尚未开启"}
                    </strong>
                    <span className="mt-0.5 block text-[10px] text-white/50">
                      {active
                        ? `${status?.pairedDeviceCount ?? 0} 台已配对 · ${status?.onlineDeviceCount ?? 0} 台在线`
                        : "默认不开放网络监听，开启后才能配对设备"}
                    </span>
                  </div>
                </div>
                {!status?.enabled && managed ? (
                  <button
                    type="button"
                    onClick={() => setPendingAction({ kind: "enable" })}
                    className="h-9 rounded-full bg-white px-4 text-[11px] font-semibold text-blue-700 transition hover:bg-blue-50"
                  >
                    开启连接…
                  </button>
                ) : active ? (
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setPendingAction({ kind: "pair" })}
                      className="h-9 rounded-full bg-white px-4 text-[11px] font-semibold text-blue-700 transition hover:bg-blue-50"
                    >
                      添加设备…
                    </button>
                    {status?.canManageListener && (
                      <button
                        type="button"
                        onClick={() => setPendingAction({ kind: "disable" })}
                        className="h-9 rounded-full bg-white/10 px-3.5 text-[11px] font-semibold text-white/72 transition hover:bg-white/15"
                      >
                        关闭
                      </button>
                    )}
                  </div>
                ) : null}
              </section>

              {status?.startupError && (
                <p className="mt-3 rounded-xl bg-amber-50 px-3 py-2.5 text-[11px] text-amber-800 ring-1 ring-amber-100">
                  设备入口启动失败：{status.startupError}
                </p>
              )}
              {status?.mode === "agent-shared" && (
                <p className="mt-3 rounded-xl bg-blue-50 px-3 py-2.5 text-[10px] leading-4 text-blue-800 ring-1 ring-blue-100">
                  当前复用 Agent
                  开发连接：设备共享同一配对凭据，暂不能单独撤销。 原生 Echo
                  模式会自动使用一机一凭据。
                </p>
              )}

              <div className="mt-4 space-y-3">
                {status?.devices.length ? (
                  status.devices.map((device) => (
                    <DeviceCard
                      key={device.id}
                      device={device}
                      onRevoke={() =>
                        setPendingAction({ kind: "revoke", device })
                      }
                    />
                  ))
                ) : (
                  <div className="grid min-h-44 place-items-center rounded-[20px] border border-dashed border-slate-300/80 bg-white/42 px-6 text-center">
                    <div>
                      <SmartphoneIcon className="mx-auto size-8 text-slate-300" />
                      <strong className="mt-3 block text-[13px] text-slate-600">
                        还没有已配对设备
                      </strong>
                      <p className="mt-1 text-[10px] text-slate-400">
                        开启连接后，从“添加设备”创建配对邀请。
                      </p>
                    </div>
                  </div>
                )}
              </div>
            </>
          ) : section === "pair" ? (
            <div className="mt-5">
              {!active ? (
                <section className="rounded-[22px] bg-white/72 p-6 text-center ring-1 ring-white">
                  <RouterIcon className="mx-auto size-9 text-slate-300" />
                  <h2 className="mt-3 text-[15px] font-semibold">
                    先开启局域网设备连接
                  </h2>
                  <p className="mx-auto mt-1 max-w-sm text-[11px] leading-5 text-slate-500">
                    Echo 默认不开放额外端口。开启后，手机才能在同一 Wi-Fi
                    中找到这台设备。
                  </p>
                  {managed && (
                    <button
                      type="button"
                      onClick={() => setPendingAction({ kind: "enable" })}
                      className="mt-4 h-9 rounded-full bg-blue-600 px-5 text-[11px] font-semibold text-white hover:bg-blue-700"
                    >
                      开启连接…
                    </button>
                  )}
                </section>
              ) : invitation ? (
                <section className="rounded-[22px] bg-white/78 p-6 shadow-sm ring-1 ring-white">
                  <div className="flex items-start gap-3">
                    <span className="grid size-10 shrink-0 place-items-center rounded-[14px] bg-emerald-50 text-emerald-600">
                      <CheckIcon className="size-5" />
                    </span>
                    <div>
                      <h2 className="text-[15px] font-semibold">
                        配对邀请已创建
                      </h2>
                      <p className="mt-1 text-[10px] leading-4 text-slate-500">
                        在 Echo Mobile 的“连接设备”中粘贴以下链接。
                        {invitation.expiresAt
                          ? " 邀请将在 5 分钟后失效；首次连接后只绑定该设备。"
                          : " 当前为 Agent 兼容模式，链接使用共享设备凭据。"}
                      </p>
                    </div>
                  </div>
                  <div className="mt-5 rounded-[16px] bg-slate-950 p-4 text-white shadow-inner">
                    <code className="block break-all text-[10px] leading-5 text-white/72">
                      {invitation.connectString}
                    </code>
                    <div className="mt-3 flex items-center justify-between gap-3 border-t border-white/10 pt-3">
                      <span className="text-[9px] text-white/35">
                        {invitation.credentialMode === "per-device"
                          ? `有效至 ${timeLabel(invitation.expiresAt)}`
                          : "共享凭据 · 仅开发兼容模式"}
                      </span>
                      <button
                        type="button"
                        onClick={() => {
                          void navigator.clipboard
                            .writeText(invitation.connectString)
                            .then(() => {
                              setCopied(true);
                              window.setTimeout(() => setCopied(false), 1_500);
                            });
                        }}
                        className="inline-flex h-8 items-center gap-1.5 rounded-full bg-white/12 px-3 text-[10px] font-semibold text-white transition hover:bg-white/18"
                      >
                        {copied ? (
                          <CheckIcon className="size-3.5" />
                        ) : (
                          <ClipboardIcon className="size-3.5" />
                        )}
                        {copied ? "已复制" : "复制配对链接"}
                      </button>
                    </div>
                  </div>
                  <p className="mt-3 flex items-start gap-1.5 text-[9px] leading-4 text-slate-400">
                    <LockKeyholeIcon className="mt-0.5 size-3.5 shrink-0" />
                    <span>
                      配对链接包含设备凭据，请勿发送到群聊或公开网络。状态页与日志不会记录该链接。
                      {invitation.deviceSync
                        ? ` 自动备份入口已一并配置（${
                            invitation.deviceSync.transport === "tailnet-https"
                              ? "Tailscale 私网 HTTPS"
                              : "可信局域网"
                          }）。`
                        : " 当前共享凭据模式不提供自动备份。"}
                    </span>
                  </p>
                </section>
              ) : (
                <section className="rounded-[22px] bg-white/72 p-6 text-center ring-1 ring-white">
                  <Link2Icon className="mx-auto size-9 text-blue-500" />
                  <h2 className="mt-3 text-[15px] font-semibold">
                    连接一台新设备
                  </h2>
                  <p className="mx-auto mt-1 max-w-md text-[11px] leading-5 text-slate-500">
                    创建短时配对邀请。原生 Echo
                    模式下，邀请只会绑定首次使用它的设备，之后可单独撤销。
                  </p>
                  <button
                    type="button"
                    onClick={() => setPendingAction({ kind: "pair" })}
                    className="mt-4 h-9 rounded-full bg-blue-600 px-5 text-[11px] font-semibold text-white hover:bg-blue-700"
                  >
                    创建配对邀请…
                  </button>
                </section>
              )}
            </div>
          ) : section === "sync" ? (
            <div className="mt-5 space-y-4">
              <section className="rounded-[22px] bg-gradient-to-br from-blue-600 via-indigo-600 to-violet-700 p-5 text-white shadow-[0_18px_50px_rgba(67,56,202,.22)]">
                <div className="flex items-start gap-4">
                  <span className="grid size-12 shrink-0 place-items-center rounded-[16px] bg-white/14">
                    <UploadCloudIcon className="size-6" />
                  </span>
                  <div>
                    <span className="rounded-full bg-emerald-300/16 px-2.5 py-1 text-[9px] font-semibold text-emerald-100">
                      服务端同步已就绪
                    </span>
                    <h2 className="mt-3 text-[18px] font-semibold">
                      每台设备、每类内容单独授权
                    </h2>
                    <p className="mt-1 max-w-lg text-[11px] leading-5 text-white/62">
                      手机凭据只可写入自己的备份目录。传输中断会从已保存位置继续；同名但内容不同的文件保留两份，不静默覆盖。
                    </p>
                  </div>
                </div>
              </section>

              {!syncStatus ? (
                <section className="rounded-[20px] bg-white/72 p-5 text-center ring-1 ring-white">
                  <UploadCloudIcon className="mx-auto size-8 text-slate-300" />
                  <strong className="mt-3 block text-[13px] text-slate-600">
                    自动备份服务暂不可用
                  </strong>
                  <p className="mt-1 text-[10px] text-slate-400">
                    请确认文件存储已经挂载，然后刷新状态。
                  </p>
                </section>
              ) : !syncStatus.available ? (
                <section className="rounded-[20px] bg-amber-50 p-5 ring-1 ring-amber-100">
                  <strong className="text-[13px] text-amber-900">
                    当前 Agent 兼容模式不能开启自动备份
                  </strong>
                  <p className="mt-1 text-[10px] leading-5 text-amber-700">
                    共享凭据无法判断是哪台手机，也不能单独撤销。原生 Echo
                    的一机一凭据模式才会开放写入。
                  </p>
                </section>
              ) : syncStatus.devices.length ? (
                syncStatus.devices.map((device) => (
                  <section
                    key={device.id}
                    className="rounded-[20px] bg-white/78 p-4 shadow-[0_10px_30px_rgba(51,65,85,.06)] ring-1 ring-white"
                  >
                    <div className="flex items-center justify-between gap-3 border-b border-slate-100 pb-3">
                      <div className="flex min-w-0 items-center gap-3">
                        <span className="grid size-9 shrink-0 place-items-center rounded-[13px] bg-blue-50 text-blue-600">
                          <SmartphoneIcon className="size-4.5" />
                        </span>
                        <span className="min-w-0">
                          <strong className="block truncate text-[13px]">
                            {device.name}
                          </strong>
                          <small className="block truncate text-[9px] text-slate-400">
                            {device.id}
                          </small>
                        </span>
                      </div>
                      <span
                        className={cn(
                          "rounded-full px-2 py-1 text-[9px] font-semibold",
                          device.online
                            ? "bg-emerald-50 text-emerald-700"
                            : "bg-slate-100 text-slate-500",
                        )}
                      >
                        {device.online ? "在线" : "离线可续传"}
                      </span>
                    </div>
                    <div className="divide-y divide-slate-100">
                      {(
                        [
                          ["photos", ImagesIcon, "照片", "相机照片与图片"],
                          ["files", FileIcon, "文件", "文档与其他文件"],
                        ] as const
                      ).map(([scope, Icon, label, description]) => {
                        const enabled = device.grants[scope];
                        const summary = device.summary[scope];
                        return (
                          <div
                            key={scope}
                            className="flex items-center gap-3 py-3 first:pt-3 last:pb-0"
                          >
                            <span
                              className={cn(
                                "grid size-9 shrink-0 place-items-center rounded-xl",
                                scope === "photos"
                                  ? "bg-rose-50 text-rose-500"
                                  : "bg-amber-50 text-amber-600",
                              )}
                            >
                              <Icon className="size-4.5" />
                            </span>
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-2">
                                <strong className="text-[12px]">{label}</strong>
                                {summary.conflicts > 0 && (
                                  <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[8px] font-semibold text-amber-700">
                                    {summary.conflicts} 个冲突已保留
                                  </span>
                                )}
                              </div>
                              <p className="mt-0.5 text-[9px] text-slate-400">
                                {enabled
                                  ? `${summary.committed} 项 · ${bytesLabel(summary.bytes)}${summary.uploading ? ` · ${summary.uploading} 项续传中` : ""}`
                                  : description}
                              </p>
                            </div>
                            <button
                              type="button"
                              aria-label={`${enabled ? "关闭" : "开启"}${label}备份：${device.name}`}
                              onClick={() =>
                                setPendingAction({
                                  kind: "sync",
                                  device,
                                  scope,
                                  enabled: !enabled,
                                })
                              }
                              className={cn(
                                "h-8 rounded-full px-3 text-[9px] font-semibold transition",
                                enabled
                                  ? "bg-emerald-50 text-emerald-700 hover:bg-emerald-100"
                                  : "bg-blue-600 text-white hover:bg-blue-700",
                              )}
                            >
                              {enabled ? "已开启" : "开启…"}
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  </section>
                ))
              ) : (
                <section className="rounded-[20px] border border-dashed border-slate-300/80 bg-white/42 p-6 text-center">
                  <SmartphoneIcon className="mx-auto size-8 text-slate-300" />
                  <strong className="mt-3 block text-[13px] text-slate-600">
                    配对设备后再开启备份
                  </strong>
                  <p className="mt-1 text-[10px] text-slate-400">
                    只有原生一机一凭据的设备才会出现在这里。
                  </p>
                </section>
              )}

              <p className="flex items-start gap-1.5 px-1 text-[9px] leading-4 text-slate-400">
                <ShieldCheckIcon className="mt-0.5 size-3.5 shrink-0 text-emerald-500" />
                Echo 只保存自己的同步账本，不直接读写 Agent
                私有数据库。照片完成后会自动出现在照片库；移动端需使用本版本同步协议。
              </p>
            </div>
          ) : (
            <div className="mt-5 space-y-4">
              <section className="rounded-[22px] bg-gradient-to-br from-slate-900 via-slate-800 to-indigo-950 p-6 text-white shadow-[0_18px_50px_rgba(15,23,42,.2)]">
                <div className="flex items-start gap-4">
                  <span className="grid size-12 shrink-0 place-items-center rounded-[16px] bg-white/10">
                    <Globe2Icon className="size-6 text-blue-200" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <span
                      className={cn(
                        "rounded-full px-2.5 py-1 text-[9px] font-semibold",
                        remoteConnected
                          ? "bg-emerald-300/15 text-emerald-100"
                          : remote?.configured
                            ? "bg-blue-300/15 text-blue-100"
                            : "bg-amber-300/15 text-amber-100",
                      )}
                    >
                      {remoteConnected
                        ? "私网已连接"
                        : remote?.configured
                          ? "正在连接"
                          : "尚未配置"}
                    </span>
                    <h2 className="mt-3 text-[18px] font-semibold">
                      {remoteConnected
                        ? "已可从个人私网访问 Echo"
                        : remote?.configured
                          ? "正在等待 Tailscale 完成授权"
                          : "当前只支持同一局域网"}
                    </h2>
                    <p className="mt-1 max-w-lg text-[11px] leading-5 text-white/55">
                      {remoteConnected
                        ? "Echo 桌面通过 Tailscale 的 WireGuard 私网和 HTTPS 提供访问，不开放公网匿名入口。"
                        : remote?.configured
                          ? "安全网关已经配置，但尚未取得可用的私网地址。请检查 Tailscale 授权和网络连接。"
                          : "Echo 还没有启用私有网络或云中继，因此外网下不会暴露这台设备，也不会把局域网连接误报成“远程访问”。"}
                    </p>
                    {remoteConnected && remote?.endpoint && (
                      <a
                        href={remote.endpoint}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-4 inline-flex h-9 items-center gap-1.5 rounded-full bg-white px-4 text-[10px] font-semibold text-indigo-700 transition hover:bg-indigo-50"
                      >
                        打开远程地址
                        <ExternalLinkIcon className="size-3.5" />
                      </a>
                    )}
                  </div>
                </div>
              </section>
              <section className="grid gap-3 sm:grid-cols-2">
                <article className="rounded-[18px] bg-white/75 p-4 ring-1 ring-white">
                  <span className="grid size-9 place-items-center rounded-xl bg-emerald-50 text-emerald-600">
                    <ShieldCheckIcon className="size-4.5" />
                  </span>
                  <strong className="mt-3 block text-[13px]">
                    {remoteConnected ? "远程网页已经具备" : "现在已经具备"}
                  </strong>
                  <p className="mt-1 text-[10px] leading-5 text-slate-500">
                    {remoteConnected
                      ? "桌面、文件管理和照片页面可通过个人 Tailnet 加密访问；入口仍受 Echo 登录、Origin 防护和管理员审批保护。"
                      : "管理员复核、短时邀请、一机一凭据、单设备撤销，以及 Agent Tentacle 的在线状态和能力上报。"}
                  </p>
                </article>
                <article className="rounded-[18px] bg-white/75 p-4 ring-1 ring-white">
                  <span className="grid size-9 place-items-center rounded-xl bg-blue-50 text-blue-600">
                    <RouterIcon className="size-4.5" />
                  </span>
                  <strong className="mt-3 block text-[13px]">
                    {remoteConnected ? "仍然保持边界" : "下一步还要补"}
                  </strong>
                  <p className="mt-1 text-[10px] leading-5 text-slate-500">
                    {remoteConnected
                      ? remote?.features.fileSync && remote.features.photoSync
                        ? "手机 Tentacle 控制端口仍只在局域网开放；已授权设备可通过 Tailnet HTTPS 断点备份文件与照片。"
                        : "手机 Tentacle 控制端口仍只在局域网开放；当前远程网关还未挂载设备备份接口。"
                      : "Tailscale 私网或受控中继、HTTPS、外网可达性检测，以及文件与照片的断点同步。"}
                  </p>
                </article>
              </section>
              <p className="flex items-center gap-1.5 px-1 text-[9px] text-slate-400">
                <WifiIcon className="size-3.5" />
                {remoteConnected
                  ? "远程桌面：WireGuard + HTTPS · 设备配对：局域网凭据认证"
                  : `设备配对：${status?.transport.protocol ?? "websocket"} · 凭据认证 · ${status?.transport.encrypted ? "已加密" : "局域网未加密"}`}
              </p>
            </div>
          )}
        </main>
      </section>

      <HighRiskApprovalDialog
        open={!!pendingAction}
        title={approvalCopy?.title ?? "确认设备连接操作"}
        description={approvalCopy?.description ?? "请输入管理员密码继续。"}
        targetLabel={approvalCopy?.target}
        confirmLabel={approvalCopy?.confirm}
        destructive={approvalCopy?.destructive}
        onCancel={() => setPendingAction(null)}
        onConfirm={runApprovedAction}
      />
    </div>
  );
}
