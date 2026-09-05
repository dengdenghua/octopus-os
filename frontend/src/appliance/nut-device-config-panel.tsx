import { useCallback, useEffect, useState } from "react";
import { CableIcon, Loader2Icon } from "lucide-react";

import { requestHighRiskApproval } from "@/appliance/approval";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";
import {
  applyNutDeviceConfig,
  fetchNutDeviceConfig,
  planNutDeviceConfig,
  type NutDeviceConfigPlan,
  type NutDeviceConfigStatus,
  type NutDeviceDesired,
  type NutUsbDriver,
} from "@/appliance/nut-device-config";
import { fetchNativeStatus } from "@/appliance/omv";

const CAPABILITY = "power.ups.local-usb.configure.v1";
const DRIVER_OPTIONS: Array<{ value: NutUsbDriver; label: string }> = [
  { value: "usbhid-ups", label: "通用 USB HID（推荐）" },
  { value: "nutdrv_qx", label: "Voltronic / Qx USB" },
  { value: "blazer_usb", label: "Megatec / Blazer USB" },
  { value: "bcmxcp_usb", label: "Eaton BCMXCP USB" },
  { value: "richcomm_usb", label: "Richcomm USB" },
  { value: "tripplite_usb", label: "Tripp Lite USB" },
];

export function NutDeviceConfigPanel() {
  const [status, setStatus] = useState<NutDeviceConfigStatus | null>(null);
  const [driver, setDriver] = useState<NutUsbDriver>("usbhid-ups");
  const [capable, setCapable] = useState(false);
  const [pendingDesired, setPendingDesired] = useState<NutDeviceDesired | null>(
    null,
  );
  const [pendingPlan, setPendingPlan] = useState<NutDeviceConfigPlan | null>(
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
        fetchNutDeviceConfig(),
        fetchNativeStatus(),
      ]);
      setStatus(nextStatus);
      if (nextStatus.driver) setDriver(nextStatus.driver);
      setCapable(native.capabilities.includes(CAPABILITY));
    } catch (reason) {
      setStatus(null);
      setError(
        reason instanceof Error ? reason.message : "无法读取本机 UPS 配置",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const preview = async (enabled: boolean) => {
    const desired: NutDeviceDesired = {
      schema: "echo.nut-local-ups-desired.v1",
      enabled,
      driver,
    };
    setBusy(true);
    setError(null);
    try {
      const plan = await planNutDeviceConfig(desired);
      if (plan.operation === "none") {
        await refresh();
        return;
      }
      setPendingDesired(desired);
      setPendingPlan(plan);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法预览本机 UPS 配置",
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
        "power.ups.local-usb.configure",
        pendingPlan.planId,
        password,
      );
      await applyNutDeviceConfig(
        pendingDesired,
        pendingPlan.planId,
        approval.approvalToken,
      );
      setPendingDesired(null);
      setPendingPlan(null);
      await refresh();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法更新本机 UPS 配置",
      );
      throw reason;
    } finally {
      setBusy(false);
    }
  };

  const blocked =
    !capable ||
    status?.externallyManaged === true ||
    status?.localOnly === false;
  return (
    <>
      <section className="mt-5 rounded-[22px] bg-white/80 p-5 shadow-sm ring-1 ring-white/90">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <CableIcon className="size-5 text-indigo-600" /> 本机 USB UPS
            </h2>
            <p className="mt-1 max-w-2xl text-[11px] leading-5 text-slate-500">
              {status?.enabled
                ? `已登记 echo-ups，驱动 ${status.driver}，端口自动探测；数据服务仅监听本机回环地址。`
                : "登记一台直连 USB UPS。不会配置网络 UPS、串口路径或 NUT upsmon；自动关机仍由上方独立审批策略控制。"}
            </p>
          </div>
          <span
            className={`rounded-full px-2.5 py-1 text-[10px] font-semibold ${
              status?.enabled
                ? "bg-emerald-100 text-emerald-800"
                : "bg-slate-200 text-slate-600"
            }`}
          >
            {status?.enabled ? "已登记" : "未登记"}
          </span>
        </div>

        <div className="mt-4 flex flex-wrap items-end gap-3">
          <label className="min-w-56 text-[11px] font-medium text-slate-600">
            USB 驱动
            <select
              aria-label="USB UPS 驱动"
              value={driver}
              disabled={loading || busy || status?.enabled || blocked}
              onChange={(event) =>
                setDriver(event.target.value as NutUsbDriver)
              }
              className="mt-1 block h-9 w-full rounded-lg border border-slate-300 bg-white px-2 text-xs text-slate-700 disabled:opacity-50"
            >
              {DRIVER_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={loading || busy || !status || blocked}
            onClick={() => status && void preview(!status.enabled)}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 text-[11px] font-semibold text-slate-700 disabled:opacity-40"
          >
            {(loading || busy) && (
              <Loader2Icon className="size-3 animate-spin" />
            )}
            {status?.enabled ? "移除本机 UPS" : "登记本机 UPS"}
          </button>
        </div>

        {status?.externallyManaged && (
          <p className="mt-3 rounded-xl bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
            检测到非 Echo 管理的 NUT
            设备段，为避免覆盖现有配置，本页面保持只读。
          </p>
        )}
        {status?.localOnly === false && (
          <p className="mt-3 rounded-xl bg-red-50 px-3 py-2 text-[11px] text-red-700">
            upsd 正在监听非回环地址；Echo 拒绝接管可能暴露到局域网的配置。
          </p>
        )}
        {!loading && !capable && !status?.externallyManaged && (
          <p className="mt-3 rounded-xl bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
            本机 NUT 驱动或 systemd 工具不完整，暂不能登记 UPS。
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
          pendingDesired?.enabled ? "确认登记本机 UPS" : "确认移除本机 UPS"
        }
        description="此操作会更新 root 管理的 NUT 驱动与回环监听配置，并重启本机 NUT 驱动和数据服务；失败会自动恢复原配置。"
        targetLabel={`echo-ups · ${pendingDesired?.driver ?? driver} · port=auto`}
        confirmLabel={pendingDesired?.enabled ? "确认登记" : "确认移除"}
        onCancel={() => {
          setPendingDesired(null);
          setPendingPlan(null);
        }}
        onConfirm={apply}
      />
    </>
  );
}
