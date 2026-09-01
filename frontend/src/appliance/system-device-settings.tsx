import { useEffect, useState } from "react";
import {
  BatteryChargingIcon,
  BellIcon,
  BluetoothIcon,
  CheckCircle2Icon,
  DownloadIcon,
  LockKeyholeIcon,
  MonitorIcon,
  PaletteIcon,
  RefreshCwIcon,
  Volume2Icon,
  WifiIcon,
} from "lucide-react";

import type {
  SystemControlState,
  SystemUpdateCapabilities,
  SystemUpdateStatus,
} from "@/types/electron";

export type SystemDeviceSettingsSection =
  | "connectivity"
  | "displaySound"
  | "energy"
  | "wallpaper"
  | "notificationsLock"
  | "general";

export const SYSTEM_DEVICE_SETTINGS_ITEMS = [
  { id: "connectivity", label: "连接与设备", icon: WifiIcon },
  { id: "displaySound", label: "显示与声音", icon: MonitorIcon },
  { id: "energy", label: "电池与能耗", icon: BatteryChargingIcon },
  { id: "wallpaper", label: "壁纸与桌面", icon: PaletteIcon },
  { id: "notificationsLock", label: "通知与锁屏", icon: BellIcon },
  { id: "general", label: "通用与更新", icon: RefreshCwIcon },
] as const satisfies ReadonlyArray<{
  id: SystemDeviceSettingsSection;
  label: string;
  icon: typeof WifiIcon;
}>;

type Wallpaper = "orbit" | "aurora" | "sunset" | "midnight";

export interface SystemDeviceSettingsProps {
  section: SystemDeviceSettingsSection;
  controls?: SystemControlState | null;
  wallpaper?: Wallpaper;
  notificationCount?: number;
  notificationServiceAvailable?: boolean;
  updateCapabilities?: SystemUpdateCapabilities | null;
  updateStatus?: SystemUpdateStatus | null;
  updateBusy?: boolean;
  lockAvailable?: boolean;
  onSetWifiEnabled?: (enabled: boolean) => Promise<unknown> | unknown;
  onSetBluetoothEnabled?: (enabled: boolean) => Promise<unknown> | unknown;
  onSetAudioVolume?: (percentage: number) => Promise<unknown> | unknown;
  onSetDisplayBrightness?: (percentage: number) => Promise<unknown> | unknown;
  onWallpaperChange?: (wallpaper: Wallpaper) => void;
  onOpenNotifications?: () => void;
  onLock?: () => void;
  onRefreshUpdate?: () => void;
  onApplyUpdate?: () => void;
}

function SettingsCard({ children }: { children: React.ReactNode }) {
  return (
    <section className="overflow-hidden rounded-2xl border border-slate-200/90 bg-white shadow-sm">
      {children}
    </section>
  );
}

function SettingsRow({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: typeof WifiIcon;
  title: string;
  description: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex min-h-20 items-center gap-4 border-b border-slate-100 px-5 py-4 last:border-b-0">
      <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-blue-50 text-blue-600">
        <Icon className="size-5" />
      </span>
      <span className="min-w-0 flex-1">
        <strong className="block text-sm font-semibold">{title}</strong>
        <small className="mt-1 block text-xs leading-5 text-slate-500">
          {description}
        </small>
      </span>
      {children ? <span className="shrink-0">{children}</span> : null}
    </div>
  );
}

function Toggle({
  checked,
  disabled,
  label,
  onChange,
}: {
  checked: boolean;
  disabled?: boolean;
  label: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-7 w-12 rounded-full transition disabled:cursor-not-allowed disabled:opacity-40 ${
        checked ? "bg-blue-600" : "bg-slate-300"
      }`}
    >
      <span
        className={`absolute top-1 size-5 rounded-full bg-white shadow transition ${
          checked ? "left-6" : "left-1"
        }`}
      />
    </button>
  );
}

function ControlSlider({
  label,
  value,
  disabled,
  onCommit,
}: {
  label: string;
  value: number;
  disabled?: boolean;
  onCommit?: (value: number) => Promise<unknown> | unknown;
}) {
  const [preview, setPreview] = useState(value);
  useEffect(() => setPreview(value), [value]);
  return (
    <div className="flex w-64 items-center gap-3">
      <input
        aria-label={label}
        className="w-full accent-blue-600"
        type="range"
        min={0}
        max={100}
        value={preview}
        disabled={disabled}
        onChange={(event) => setPreview(Number(event.currentTarget.value))}
        onPointerUp={(event) => void onCommit?.(Number(event.currentTarget.value))}
        onKeyUp={(event) => void onCommit?.(Number(event.currentTarget.value))}
      />
      <span className="w-10 text-right text-xs tabular-nums text-slate-500">
        {preview}%
      </span>
    </div>
  );
}

const WALLPAPERS: Array<{
  id: Wallpaper;
  label: string;
  gradient: string;
}> = [
  {
    id: "orbit",
    label: "轨道",
    gradient: "linear-gradient(135deg,#1d4ed8,#67e8f9 55%,#f8fafc)",
  },
  {
    id: "aurora",
    label: "极光",
    gradient: "linear-gradient(135deg,#081f3d,#16a34a 52%,#a7f3d0)",
  },
  {
    id: "sunset",
    label: "日落",
    gradient: "linear-gradient(135deg,#7c2d12,#fb7185 52%,#fde68a)",
  },
  {
    id: "midnight",
    label: "午夜",
    gradient: "linear-gradient(135deg,#020617,#312e81 55%,#0f172a)",
  },
];

function updateStateLabel(status?: SystemUpdateStatus | null) {
  if (!status) return "尚未读取更新状态";
  if (status.state === "ready") return `发现新版本${status.version ? ` ${status.version}` : ""}`;
  if (status.state === "checking") return "正在检查更新…";
  if (status.state === "installing") return "正在安装更新…";
  if (status.state === "reboot-required") return "更新已就绪，需要重新启动";
  if (status.state === "failed") return status.error || "更新失败";
  if (status.state === "unavailable") return status.error || "当前环境不支持系统更新";
  return "系统已是最新状态";
}

export function SystemDeviceSettings(props: SystemDeviceSettingsProps) {
  const controls = props.controls;
  const native = controls?.nativeShell === true;

  if (props.section === "connectivity") {
    const wifiAvailable = Boolean(native && controls?.wifi.available);
    const bluetoothAvailable = Boolean(
      native && controls?.bluetooth.available && controls.bluetooth.present,
    );
    return (
      <SettingsCard>
        <SettingsRow
          icon={WifiIcon}
          title="Wi-Fi"
          description={
            wifiAvailable
              ? controls?.wifi.connection || "已打开，当前未连接网络"
              : "仅在 Echo OS 原生 Linux 会话中可用"
          }
        >
          <Toggle
            label="Wi-Fi"
            checked={controls?.wifi.enabled === true}
            disabled={!wifiAvailable}
            onChange={(enabled) => void props.onSetWifiEnabled?.(enabled)}
          />
        </SettingsRow>
        <SettingsRow
          icon={BluetoothIcon}
          title="蓝牙"
          description={
            bluetoothAvailable
              ? controls?.bluetooth.controller || "蓝牙控制器已就绪"
              : "未检测到可管理的蓝牙控制器"
          }
        >
          <Toggle
            label="蓝牙"
            checked={controls?.bluetooth.enabled === true}
            disabled={!bluetoothAvailable}
            onChange={(enabled) => void props.onSetBluetoothEnabled?.(enabled)}
          />
        </SettingsRow>
      </SettingsCard>
    );
  }

  if (props.section === "displaySound") {
    return (
      <SettingsCard>
        <SettingsRow
          icon={MonitorIcon}
          title="显示器亮度"
          description={controls?.display.available ? "调节内置显示器亮度" : "当前显示器不支持系统亮度控制"}
        >
          <ControlSlider
            label="显示器亮度"
            value={controls?.display.brightness ?? 70}
            disabled={!controls?.display.available}
            onCommit={props.onSetDisplayBrightness}
          />
        </SettingsRow>
        <SettingsRow
          icon={Volume2Icon}
          title="系统音量"
          description={controls?.audio.available ? "调节当前默认音频输出" : "当前环境不支持系统音量控制"}
        >
          <ControlSlider
            label="系统音量"
            value={controls?.audio.volume ?? 40}
            disabled={!controls?.audio.available}
            onCommit={props.onSetAudioVolume}
          />
        </SettingsRow>
      </SettingsCard>
    );
  }

  if (props.section === "energy") {
    const battery = controls?.battery;
    return (
      <SettingsCard>
        <SettingsRow
          icon={BatteryChargingIcon}
          title="电池状态"
          description={
            battery?.available && battery.present
              ? `${battery.state || "状态未知"} · 剩余 ${battery.percentage ?? "--"}%`
              : "未检测到电池，设备可能正在使用外接电源"
          }
        >
          {battery?.present && typeof battery.percentage === "number" ? (
            <strong className="text-2xl font-semibold tabular-nums text-slate-800">
              {battery.percentage}%
            </strong>
          ) : null}
        </SettingsRow>
        <SettingsRow
          icon={CheckCircle2Icon}
          title="节能状态"
          description="系统会跟随 Linux 电源管理策略，减少不必要的后台刷新与动画。"
        />
      </SettingsCard>
    );
  }

  if (props.section === "wallpaper") {
    return (
      <SettingsCard>
        <div className="p-5">
          <h2 className="text-sm font-semibold">桌面壁纸</h2>
          <p className="mt-1 text-xs text-slate-500">
            液态玻璃效果统一采样当前系统壁纸，不再使用独立工作台主题。
          </p>
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {WALLPAPERS.map((item) => {
              const selected = props.wallpaper === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => props.onWallpaperChange?.(item.id)}
                  className={`overflow-hidden rounded-xl border bg-white p-1.5 text-left transition ${
                    selected
                      ? "border-blue-600 ring-2 ring-blue-600/20"
                      : "border-slate-200 hover:border-slate-400"
                  }`}
                >
                  <span
                    className="block aspect-[16/10] rounded-lg"
                    style={{ background: item.gradient }}
                  />
                  <span className="mt-2 flex items-center justify-between px-1 text-xs font-medium">
                    {item.label}
                    {selected ? <CheckCircle2Icon className="size-4 text-blue-600" /> : null}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </SettingsCard>
    );
  }

  if (props.section === "notificationsLock") {
    return (
      <SettingsCard>
        <SettingsRow
          icon={BellIcon}
          title="通知中心"
          description={
            props.notificationServiceAvailable
              ? `当前有 ${props.notificationCount ?? 0} 条系统通知`
              : "原生通知服务当前不可用"
          }
        >
          <button
            type="button"
            disabled={!props.notificationServiceAvailable}
            onClick={props.onOpenNotifications}
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-medium hover:bg-slate-50 disabled:opacity-40"
          >
            打开通知中心
          </button>
        </SettingsRow>
        <SettingsRow
          icon={LockKeyholeIcon}
          title="锁定屏幕"
          description="立即暂停当前桌面会话，返回系统登录界面。"
        >
          <button
            type="button"
            disabled={!props.lockAvailable}
            onClick={props.onLock}
            className="rounded-lg bg-slate-900 px-3 py-2 text-xs font-medium text-white hover:bg-slate-700 disabled:opacity-40"
          >
            立即锁屏
          </button>
        </SettingsRow>
      </SettingsCard>
    );
  }

  const canApply = Boolean(props.updateCapabilities?.apply);
  return (
    <SettingsCard>
      <SettingsRow
        icon={RefreshCwIcon}
        title="系统更新"
        description={updateStateLabel(props.updateStatus)}
      >
        <div className="flex gap-2">
          <button
            type="button"
            disabled={props.updateBusy}
            onClick={props.onRefreshUpdate}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-medium hover:bg-slate-50 disabled:opacity-40"
          >
            <RefreshCwIcon className={`size-3.5 ${props.updateBusy ? "animate-spin" : ""}`} />
            检查更新
          </button>
          {props.updateStatus?.state === "ready" ? (
            <button
              type="button"
              disabled={!canApply || props.updateBusy}
              onClick={props.onApplyUpdate}
              className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-40"
            >
              <DownloadIcon className="size-3.5" />
              安装更新
            </button>
          ) : null}
        </div>
      </SettingsRow>
      <SettingsRow
        icon={CheckCircle2Icon}
        title="Echo OS"
        description="Agent、文件、存储和自动化能力由同一系统会话统一管理。"
      />
    </SettingsCard>
  );
}
