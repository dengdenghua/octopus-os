import { useEffect, useState } from "react";
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  Loader2Icon,
  SendIcon,
} from "lucide-react";

import { requestHighRiskApproval } from "@/appliance/approval";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";
import {
  applyNasAlertDelivery,
  fetchNasAlertDeliveryStatus,
  NAS_ALERT_CONFIGURE_ACTION,
  NAS_ALERT_TEST_ACTION,
  planNasAlertDelivery,
  testNasAlertDelivery,
  type NasAlertDeliveryPlan,
  type NasAlertDeliveryStatus,
} from "@/appliance/nas-alert-delivery";

type PendingAction =
  | { kind: "configure"; plan: NasAlertDeliveryPlan; enabled: boolean }
  | { kind: "test"; revision: string }
  | null;

export function NasAlertDeliveryPanel() {
  const [status, setStatus] = useState<NasAlertDeliveryStatus | null>(null);
  const [url, setUrl] = useState("");
  const [bearerToken, setBearerToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingAction>(null);

  const refresh = async () => {
    const next = await fetchNasAlertDeliveryStatus();
    setStatus(next);
    return next;
  };

  useEffect(() => {
    let active = true;
    void fetchNasAlertDeliveryStatus()
      .then((next) => {
        if (active) setStatus(next);
      })
      .catch((reason) => {
        if (active)
          setError(
            reason instanceof Error ? reason.message : "无法读取外部告警状态",
          );
      });
    return () => {
      active = false;
    };
  }, []);

  const preview = async (enabled: boolean) => {
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      const plan = await planNasAlertDelivery(
        enabled
          ? {
              enabled: true,
              url: url.trim(),
              ...(bearerToken ? { bearerToken } : {}),
            }
          : { enabled: false },
      );
      setPending({ kind: "configure", plan, enabled });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法生成配置预览");
    } finally {
      setBusy(false);
    }
  };

  const confirm = async (password: string) => {
    if (!pending) return;
    if (pending.kind === "test") {
      const approval = await requestHighRiskApproval(
        NAS_ALERT_TEST_ACTION,
        pending.revision,
        password,
      );
      await testNasAlertDelivery(approval.approvalToken);
      setPending(null);
      setSuccess("测试通知已由 NAS 后台服务发送");
      await refresh();
      return;
    }
    const approval = await requestHighRiskApproval(
      NAS_ALERT_CONFIGURE_ACTION,
      pending.plan.planId,
      password,
    );
    const next = await applyNasAlertDelivery(
      pending.plan.planId,
      approval.approvalToken,
    );
    setStatus(next);
    setPending(null);
    setUrl("");
    setBearerToken("");
    setSuccess(
      pending.enabled
        ? "无人值守 Webhook 告警已启用"
        : "Webhook 告警已关闭并清除密钥",
    );
  };

  const statusText = !status
    ? "正在读取后台告警状态…"
    : !status.persistenceHealthy
      ? "加密配置损坏或无法安全读取，后台投递已停止"
      : status.enabled
        ? `已启用 · ${status.destinationHost ?? "目标已脱敏"}`
        : "未启用；浏览器关闭时不会发送外部告警";

  return (
    <div className="border-b border-slate-100 px-5 py-4">
      <div className="flex items-start gap-4">
        <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-amber-50 text-amber-600">
          <SendIcon className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="text-sm font-semibold">无人值守 NAS 告警</strong>
            {status?.enabled ? (
              <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">
                后台运行
              </span>
            ) : null}
          </div>
          <p className="mt-1 text-xs leading-5 text-slate-500">{statusText}</p>
          {status?.enabled ? (
            <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px]">
              <span className="text-slate-500">
                上次成功：
                {status.lastSuccessAt
                  ? new Date(status.lastSuccessAt).toLocaleString()
                  : "尚未投递"}
              </span>
              {status.consecutiveFailures > 0 ? (
                <span className="inline-flex items-center gap-1 text-amber-700">
                  <AlertTriangleIcon className="size-3.5" /> 连续失败{" "}
                  {status.consecutiveFailures} 次
                </span>
              ) : null}
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  setPending({ kind: "test", revision: status.revision })
                }
                className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 font-medium hover:bg-slate-50 disabled:opacity-40"
              >
                发送测试通知…
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void preview(false)}
                className="rounded-lg border border-red-200 bg-red-50 px-2.5 py-1.5 font-medium text-red-700 hover:bg-red-100 disabled:opacity-40"
              >
                关闭并清除…
              </button>
            </div>
          ) : (
            <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,0.7fr)_auto]">
              <input
                type="url"
                aria-label="NAS 告警 Webhook URL"
                autoComplete="off"
                value={url}
                disabled={busy}
                onChange={(event) => setUrl(event.currentTarget.value)}
                placeholder="https://hooks.example.com/echo"
                className="h-9 min-w-0 rounded-lg border border-slate-300 bg-white px-3 text-xs outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/15"
              />
              <input
                type="password"
                aria-label="Webhook Bearer Token（可选）"
                autoComplete="new-password"
                value={bearerToken}
                disabled={busy}
                onChange={(event) => setBearerToken(event.currentTarget.value)}
                placeholder="Bearer Token（可选）"
                className="h-9 min-w-0 rounded-lg border border-slate-300 bg-white px-3 text-xs outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/15"
              />
              <button
                type="button"
                disabled={busy || !url.trim()}
                onClick={() => void preview(true)}
                className="inline-flex h-9 items-center justify-center gap-1.5 rounded-lg bg-blue-600 px-3 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-40"
              >
                {busy ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : null}
                启用…
              </button>
            </div>
          )}
          <p className="mt-2 text-[10px] leading-4 text-slate-400">
            仅允许公网 HTTPS 443，禁止重定向；完整 URL 与 Token
            只加密保存在设备私密状态中。
          </p>
          {error ? (
            <p role="alert" className="mt-2 text-xs text-red-600">
              {error}
            </p>
          ) : null}
          {success ? (
            <p
              role="status"
              className="mt-2 inline-flex items-center gap-1 text-xs text-emerald-700"
            >
              <CheckCircle2Icon className="size-3.5" /> {success}
            </p>
          ) : null}
        </div>
      </div>
      <HighRiskApprovalDialog
        open={pending !== null}
        title={
          pending?.kind === "test"
            ? "发送外部测试通知？"
            : pending?.enabled
              ? "启用无人值守告警？"
              : "关闭外部告警？"
        }
        description={
          pending?.kind === "test"
            ? "后台服务会立即向当前脱敏目标发送一条测试消息。"
            : pending?.enabled
              ? "完整 Webhook 地址和可选 Token 将加密保存，存储或 UPS 出现新告警时由后台投递。"
              : "后台投递将停止，已保存的 Webhook 地址、Token 和去重游标会被清除。"
        }
        targetLabel={
          pending?.kind === "test"
            ? (status?.destinationHost ?? "当前目标")
            : pending?.plan.planId
        }
        confirmLabel={
          pending?.kind === "test"
            ? "确认发送"
            : pending?.enabled
              ? "确认启用"
              : "确认关闭"
        }
        destructive={pending?.kind === "configure" && !pending.enabled}
        onCancel={() => setPending(null)}
        onConfirm={confirm}
      />
    </div>
  );
}
