import { useEffect, useState } from "react";
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  Loader2Icon,
  MailIcon,
} from "lucide-react";

import { requestHighRiskApproval } from "@/appliance/approval";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";
import {
  applyNasEmailAlertDelivery,
  fetchNasEmailAlertDeliveryStatus,
  NAS_EMAIL_ALERT_CONFIGURE_ACTION,
  NAS_EMAIL_ALERT_TEST_ACTION,
  planNasEmailAlertDelivery,
  testNasEmailAlertDelivery,
  type NasEmailAlertDeliveryPlan,
  type NasEmailAlertDeliveryStatus,
} from "@/appliance/nas-email-alert-delivery";

type PendingAction =
  | { kind: "configure"; plan: NasEmailAlertDeliveryPlan; enabled: boolean }
  | { kind: "test"; revision: string }
  | null;

const initialForm = {
  smtpHost: "",
  smtpPort: 465 as 465 | 587,
  username: "",
  password: "",
  fromAddress: "",
  recipient: "",
};

export function NasEmailAlertDeliveryPanel() {
  const [status, setStatus] = useState<NasEmailAlertDeliveryStatus | null>(
    null,
  );
  const [form, setForm] = useState(initialForm);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingAction>(null);

  const refresh = async () => {
    const next = await fetchNasEmailAlertDeliveryStatus();
    setStatus(next);
    return next;
  };

  useEffect(() => {
    let active = true;
    void fetchNasEmailAlertDeliveryStatus()
      .then((next) => {
        if (active) setStatus(next);
      })
      .catch((reason) => {
        if (active)
          setError(
            reason instanceof Error ? reason.message : "无法读取邮件告警状态",
          );
      });
    return () => {
      active = false;
    };
  }, []);

  const update = <Key extends keyof typeof initialForm>(
    key: Key,
    value: (typeof initialForm)[Key],
  ) => setForm((current) => ({ ...current, [key]: value }));

  const preview = async (enabled: boolean) => {
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      const plan = await planNasEmailAlertDelivery(
        enabled ? { enabled: true, ...form } : { enabled: false },
      );
      setPending({ kind: "configure", plan, enabled });
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法生成邮件配置预览",
      );
    } finally {
      setBusy(false);
    }
  };

  const confirm = async (password: string) => {
    if (!pending) return;
    if (pending.kind === "test") {
      const approval = await requestHighRiskApproval(
        NAS_EMAIL_ALERT_TEST_ACTION,
        pending.revision,
        password,
      );
      await testNasEmailAlertDelivery(approval.approvalToken);
      setPending(null);
      setSuccess("测试邮件已由 NAS 后台服务发送");
      await refresh();
      return;
    }
    const approval = await requestHighRiskApproval(
      NAS_EMAIL_ALERT_CONFIGURE_ACTION,
      pending.plan.planId,
      password,
    );
    const next = await applyNasEmailAlertDelivery(
      pending.plan.planId,
      approval.approvalToken,
    );
    setStatus(next);
    setPending(null);
    setForm(initialForm);
    setSuccess(
      pending.enabled ? "无人值守邮件告警已启用" : "邮件告警已关闭并清除凭据",
    );
  };

  const formComplete = Object.entries(form).every(([key, value]) =>
    key === "smtpPort" ? true : String(value).trim().length > 0,
  );
  const statusText = !status
    ? "正在读取邮件告警状态…"
    : !status.persistenceHealthy
      ? "加密配置损坏或无法安全读取，邮件投递已停止"
      : status.enabled
        ? `已启用 · ${status.destinationHost}:${status.smtpPort} → ${status.recipientHint}`
        : "未启用邮件告警";

  return (
    <div className="border-b border-slate-100 px-5 py-4">
      <div className="flex items-start gap-4">
        <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-sky-50 text-sky-600">
          <MailIcon className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="text-sm font-semibold">NAS 邮件告警</strong>
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
                发送测试邮件…
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
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              <input
                aria-label="SMTP 服务器"
                autoComplete="off"
                value={form.smtpHost}
                disabled={busy}
                onChange={(event) =>
                  update("smtpHost", event.currentTarget.value)
                }
                placeholder="smtp.example.com"
                className="h-9 min-w-0 rounded-lg border border-slate-300 bg-white px-3 text-xs outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/15"
              />
              <select
                aria-label="SMTP 加密端口"
                value={form.smtpPort}
                disabled={busy}
                onChange={(event) =>
                  update(
                    "smtpPort",
                    Number(event.currentTarget.value) as 465 | 587,
                  )
                }
                className="h-9 min-w-0 rounded-lg border border-slate-300 bg-white px-3 text-xs outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/15"
              >
                <option value={465}>465 · TLS</option>
                <option value={587}>587 · STARTTLS</option>
              </select>
              <input
                aria-label="SMTP 用户名"
                autoComplete="username"
                value={form.username}
                disabled={busy}
                onChange={(event) =>
                  update("username", event.currentTarget.value)
                }
                placeholder="邮箱账号"
                className="h-9 min-w-0 rounded-lg border border-slate-300 bg-white px-3 text-xs outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/15"
              />
              <input
                type="password"
                aria-label="SMTP 应用密码"
                autoComplete="new-password"
                value={form.password}
                disabled={busy}
                onChange={(event) =>
                  update("password", event.currentTarget.value)
                }
                placeholder="应用密码或授权码"
                className="h-9 min-w-0 rounded-lg border border-slate-300 bg-white px-3 text-xs outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/15"
              />
              <input
                type="email"
                aria-label="发件地址"
                autoComplete="off"
                value={form.fromAddress}
                disabled={busy}
                onChange={(event) =>
                  update("fromAddress", event.currentTarget.value)
                }
                placeholder="echo@example.com"
                className="h-9 min-w-0 rounded-lg border border-slate-300 bg-white px-3 text-xs outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/15"
              />
              <input
                type="email"
                aria-label="告警收件地址"
                autoComplete="off"
                value={form.recipient}
                disabled={busy}
                onChange={(event) =>
                  update("recipient", event.currentTarget.value)
                }
                placeholder="owner@example.com"
                className="h-9 min-w-0 rounded-lg border border-slate-300 bg-white px-3 text-xs outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/15"
              />
              <button
                type="button"
                disabled={busy || !formComplete}
                onClick={() => void preview(true)}
                className="inline-flex h-9 items-center justify-center gap-1.5 rounded-lg bg-blue-600 px-3 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-40 sm:col-span-2"
              >
                {busy ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : null}
                启用邮件告警…
              </button>
            </div>
          )}
          <p className="mt-2 text-[10px] leading-4 text-slate-400">
            仅连接全部解析为公网地址的 SMTP 域名；支持 465 TLS 或 587
            STARTTLS。账号、应用密码和地址只加密保存在设备私密状态中。
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
            ? "发送测试邮件？"
            : pending?.enabled
              ? "启用邮件告警？"
              : "关闭邮件告警？"
        }
        description={
          pending?.kind === "test"
            ? "后台服务会立即向当前脱敏收件地址发送一封测试邮件。"
            : pending?.enabled
              ? "SMTP 账号、应用密码和邮件地址将加密保存，存储或 UPS 出现新告警时由后台独立投递。"
              : "邮件投递将停止，已保存的 SMTP 账号、密码、地址和去重游标会被清除。"
        }
        targetLabel={
          pending?.kind === "test"
            ? (status?.recipientHint ?? "当前收件地址")
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
