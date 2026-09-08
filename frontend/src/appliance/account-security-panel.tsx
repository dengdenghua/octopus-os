import { useEffect, useId, useState } from "react";
import {
  BotIcon,
  FoldersIcon,
  FileClockIcon,
  HardDriveIcon,
  KeyRoundIcon,
  Loader2Icon,
  LockKeyholeIcon,
  LogOutIcon,
  ShieldCheckIcon,
  UserRoundIcon,
} from "lucide-react";

import {
  beginAdministratorTotpEnrollment,
  confirmAdministratorTotpEnrollment,
  disableAdministratorTotp,
  fetchAdministratorTotpStatus,
  revokeAllSessions,
  rotateAdminPassword,
  type AdministratorTotpEnrollment,
  type AdministratorTotpStatus,
} from "@/appliance/account-security";
import { AuditEvidencePanel } from "@/appliance/audit-evidence-panel";
import { requestHighRiskApproval } from "@/appliance/approval";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";
import { OmvStorageHealth } from "@/appliance/omv-storage-health";
import { OmvSharingPanel } from "@/appliance/omv-sharing-panel";
import {
  SYSTEM_DEVICE_SETTINGS_ITEMS,
  SystemDeviceSettings,
  type SystemDeviceSettingsProps,
  type SystemDeviceSettingsSection,
} from "@/appliance/system-device-settings";
import {
  OS_AGENT_SETTINGS_ITEMS,
  SystemAgentSettingsContent,
  type OsAgentSettingsSection,
} from "@/components/workspace/settings/system-agent-settings-content";

export type AccountSecuritySection =
  | "account"
  | "agent"
  | "models"
  | "storage"
  | "sharing"
  | "audit"
  | SystemDeviceSettingsSection;

export function AccountSecurityPanel({
  open,
  onClose,
  onSessionEnded,
  initialSection = "account",
  initialAgentSection = "models",
  systemDeviceSettings,
}: {
  open: boolean;
  onClose: () => void;
  onSessionEnded: (message: string) => void;
  initialSection?: AccountSecuritySection;
  initialAgentSection?: OsAgentSettingsSection;
  systemDeviceSettings?: Omit<SystemDeviceSettingsProps, "section">;
}) {
  const currentPasswordId = useId();
  const newPasswordId = useId();
  const confirmationId = useId();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revokeOpen, setRevokeOpen] = useState(false);
  const [totpStatus, setTotpStatus] = useState<AdministratorTotpStatus | null>(
    null,
  );
  const [totpEnrollment, setTotpEnrollment] =
    useState<AdministratorTotpEnrollment | null>(null);
  const [totpPassword, setTotpPassword] = useState("");
  const [totpFactor, setTotpFactor] = useState("");
  const [totpBusy, setTotpBusy] = useState(false);
  const [totpError, setTotpError] = useState<string | null>(null);
  const [section, setSection] =
    useState<AccountSecuritySection>(initialSection);
  const [agentSection, setAgentSection] = useState<OsAgentSettingsSection>(
    initialAgentSection === "models" ? "tools" : initialAgentSection,
  );

  useEffect(() => {
    setCurrentPassword("");
    setNewPassword("");
    setConfirmation("");
    setError(null);
    setTotpEnrollment(null);
    setTotpPassword("");
    setTotpFactor("");
    setTotpError(null);
    setSection(
      initialSection === "agent" && initialAgentSection === "models"
        ? "models"
        : initialSection,
    );
    setAgentSection(
      initialAgentSection === "models" ? "tools" : initialAgentSection,
    );
  }, [initialAgentSection, initialSection, open]);

  useEffect(() => {
    if (!open) return;
    let active = true;
    void fetchAdministratorTotpStatus()
      .then((status) => {
        if (active) setTotpStatus(status);
      })
      .catch((reason) => {
        if (active) {
          setTotpStatus(null);
          setTotpError(
            reason instanceof Error ? reason.message : "无法读取动态验证码状态",
          );
        }
      });
    return () => {
      active = false;
    };
  }, [open]);

  if (!open) return null;

  const rotate = async () => {
    setError(null);
    if (newPassword.length < 12) {
      setError("新密码至少需要 12 个字符");
      return;
    }
    if (new TextEncoder().encode(newPassword).length > 72) {
      setError("新密码最多为 72 个 UTF-8 字节");
      return;
    }
    if (newPassword !== confirmation) {
      setError("两次输入的新密码不一致");
      return;
    }
    if (currentPassword === newPassword) {
      setError("新密码不能与当前密码相同");
      return;
    }
    setBusy(true);
    try {
      const approval = await requestHighRiskApproval(
        "credentials.rotate",
        "admin",
        currentPassword,
      );
      await rotateAdminPassword(newPassword, approval.approvalToken);
      onSessionEnded("管理员密码已更新，请使用新密码重新登录");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法更新管理员密码");
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (password: string) => {
    const approval = await requestHighRiskApproval(
      "sessions.revoke",
      "all",
      password,
    );
    await revokeAllSessions(approval.approvalToken);
    setRevokeOpen(false);
    onSessionEnded("所有设备会话都已退出，请重新登录");
  };

  const beginTotp = async () => {
    if (!totpPassword || totpBusy) return;
    setTotpBusy(true);
    setTotpError(null);
    try {
      const approval = await requestHighRiskApproval(
        "credentials.totp.enroll",
        "admin",
        totpPassword,
      );
      setTotpEnrollment(
        await beginAdministratorTotpEnrollment(approval.approvalToken),
      );
      setTotpFactor("");
    } catch (reason) {
      setTotpError(
        reason instanceof Error ? reason.message : "无法开始设置动态验证码",
      );
    } finally {
      setTotpBusy(false);
    }
  };

  const confirmTotp = async () => {
    if (!totpEnrollment || !totpFactor.trim() || totpBusy) return;
    setTotpBusy(true);
    setTotpError(null);
    try {
      await confirmAdministratorTotpEnrollment(
        totpEnrollment.enrollmentId,
        totpFactor.trim(),
      );
      onSessionEnded("动态验证码已启用，请使用密码和验证码重新登录");
    } catch (reason) {
      setTotpError(
        reason instanceof Error ? reason.message : "无法启用动态验证码",
      );
    } finally {
      setTotpBusy(false);
    }
  };

  const disableTotp = async () => {
    if (!totpPassword || !totpFactor.trim() || totpBusy) return;
    setTotpBusy(true);
    setTotpError(null);
    try {
      const approval = await requestHighRiskApproval(
        "credentials.totp.disable",
        "admin",
        totpPassword,
      );
      await disableAdministratorTotp(totpFactor.trim(), approval.approvalToken);
      onSessionEnded("动态验证码已关闭，请重新登录");
    } catch (reason) {
      setTotpError(
        reason instanceof Error ? reason.message : "无法关闭动态验证码",
      );
    } finally {
      setTotpBusy(false);
    }
  };

  return (
    <div
      className="absolute inset-0 z-[80] grid place-items-center bg-black/20 px-5 pb-24 pt-12 backdrop-blur-[4px]"
      data-desktop-interactive
    >
      <div
        aria-hidden="true"
        className="absolute inset-0 cursor-default"
        onClick={() => !busy && onClose()}
      />
      <section className="relative flex h-[min(740px,calc(100vh-96px))] w-[min(1180px,calc(100vw-40px))] overflow-hidden rounded-[22px] border border-white/70 bg-[#f4f4f5]/95 text-slate-900 shadow-[0_30px_90px_rgba(15,23,42,0.38)]">
        <aside className="w-52 shrink-0 border-r border-slate-300/70 bg-white/55 px-3 py-4 backdrop-blur-2xl">
          <div className="mb-5 flex items-center gap-2 px-1">
            <div className="flex gap-2">
              <button
                type="button"
                className="size-3 rounded-full bg-[#ff5f57] ring-1 ring-black/10"
                aria-label="关闭"
                disabled={busy}
                onClick={() => !busy && onClose()}
              />
              <span className="size-3 rounded-full bg-[#febc2e] ring-1 ring-black/10" />
              <span className="size-3 rounded-full bg-[#28c840] ring-1 ring-black/10" />
            </div>
          </div>
          <div className="mb-4 flex items-center gap-3 rounded-xl bg-white/72 p-2.5 shadow-sm ring-1 ring-slate-200/70">
            <span className="grid size-9 place-items-center rounded-full bg-gradient-to-br from-sky-400 to-blue-600 text-white">
              <UserRoundIcon className="size-4.5" />
            </span>
            <span className="min-w-0">
              <strong className="block truncate text-sm">admin</strong>
              <small className="block text-[11px] text-slate-500">
                设备管理员
              </small>
            </span>
          </div>
          <button
            type="button"
            onClick={() => setSection("account")}
            className={`flex h-9 w-full items-center gap-2 rounded-lg px-3 text-left text-[13px] font-medium ${
              section === "account"
                ? "bg-blue-600 text-white shadow-sm"
                : "text-slate-700 hover:bg-white/70"
            }`}
          >
            <ShieldCheckIcon className="size-4" />
            账户与安全
          </button>
          <div className="my-2 border-t border-slate-300/60" />
          {SYSTEM_DEVICE_SETTINGS_ITEMS.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setSection(item.id)}
                className={`mt-1 flex h-9 w-full items-center gap-2 rounded-lg px-3 text-left text-[13px] font-medium ${
                  section === item.id
                    ? "bg-blue-600 text-white shadow-sm"
                    : "text-slate-700 hover:bg-white/70"
                }`}
              >
                <Icon className="size-4" />
                {item.label}
              </button>
            );
          })}
          <div className="my-2 border-t border-slate-300/60" />
          <button
            type="button"
            onClick={() => setSection("models")}
            className={`mt-1 flex h-9 w-full items-center gap-2 rounded-lg px-3 text-left text-[13px] font-medium ${section === "models" ? "bg-blue-600 text-white shadow-sm" : "text-slate-700 hover:bg-white/70"}`}
          >
            <BotIcon className="size-4" />
            模型与用量
          </button>
          <button
            type="button"
            onClick={() => setSection("agent")}
            className={`mt-1 flex h-9 w-full items-center gap-2 rounded-lg px-3 text-left text-[13px] font-medium ${
              section === "agent"
                ? "bg-blue-600 text-white shadow-sm"
                : "text-slate-700 hover:bg-white/70"
            }`}
          >
            <BotIcon className="size-4" />
            AI 与 Agent
          </button>
          <button
            type="button"
            onClick={() => setSection("storage")}
            className={`mt-1 flex h-9 w-full items-center gap-2 rounded-lg px-3 text-left text-[13px] font-medium ${
              section === "storage"
                ? "bg-blue-600 text-white shadow-sm"
                : "text-slate-700 hover:bg-white/70"
            }`}
          >
            <HardDriveIcon className="size-4" />
            存储健康
          </button>
          <button
            type="button"
            onClick={() => setSection("sharing")}
            className={`mt-1 flex h-9 w-full items-center gap-2 rounded-lg px-3 text-left text-[13px] font-medium ${
              section === "sharing"
                ? "bg-blue-600 text-white shadow-sm"
                : "text-slate-700 hover:bg-white/70"
            }`}
          >
            <FoldersIcon className="size-4" />
            共享与用户
          </button>
          <button
            type="button"
            onClick={() => setSection("audit")}
            className={`mt-1 flex h-9 w-full items-center gap-2 rounded-lg px-3 text-left text-[13px] font-medium ${
              section === "audit"
                ? "bg-blue-600 text-white shadow-sm"
                : "text-slate-700 hover:bg-white/70"
            }`}
          >
            <FileClockIcon className="size-4" />
            审计与证据
          </button>
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto px-8 py-7">
          {SYSTEM_DEVICE_SETTINGS_ITEMS.some((item) => item.id === section) ? (
            <>
              <header>
                <h1 className="text-[24px] font-semibold tracking-tight">
                  {
                    SYSTEM_DEVICE_SETTINGS_ITEMS.find(
                      (item) => item.id === section,
                    )?.label
                  }
                </h1>
                <p className="mt-1 text-[13px] text-slate-500">
                  管理 Echo OS 原生设备与桌面能力
                </p>
              </header>
              <div className="mt-6">
                <SystemDeviceSettings
                  {...systemDeviceSettings}
                  section={section as SystemDeviceSettingsSection}
                />
              </div>
            </>
          ) : section === "models" ? (
            <>
              <header>
                <h1 className="text-[24px] font-semibold tracking-tight">
                  模型与用量
                </h1>
                <p className="mt-1 text-[13px] text-slate-500">
                  管理系统模型连接、默认模型与账户用量
                </p>
              </header>
              <section className="mt-5 rounded-2xl border border-slate-200 bg-white p-5">
                <SystemAgentSettingsContent section="models" />
              </section>
            </>
          ) : section === "agent" ? (
            <>
              <header>
                <h1 className="text-[24px] font-semibold tracking-tight">
                  AI 与 Agent
                </h1>
                <p className="mt-1 text-[13px] text-slate-500">
                  统一管理 Echo 的工具、记忆、自动化与执行安全
                </p>
              </header>
              <nav
                aria-label="AI 与 Agent 设置分类"
                className="mt-5 flex flex-wrap gap-1.5 border-b border-slate-200 pb-3"
              >
                {OS_AGENT_SETTINGS_ITEMS.filter(
                  (item) => item.id !== "models",
                ).map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    aria-current={agentSection === item.id ? "page" : undefined}
                    onClick={() => setAgentSection(item.id)}
                    className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                      agentSection === item.id
                        ? "bg-blue-600 text-white shadow-sm"
                        : "bg-white/70 text-slate-600 hover:bg-white hover:text-slate-900"
                    }`}
                  >
                    {item.label}
                  </button>
                ))}
              </nav>
              <section className="mt-5 min-w-0 rounded-2xl border border-slate-200/90 bg-white p-5 shadow-sm">
                <SystemAgentSettingsContent section={agentSection} />
              </section>
            </>
          ) : section === "storage" ? (
            <OmvStorageHealth />
          ) : section === "sharing" ? (
            <OmvSharingPanel />
          ) : section === "audit" ? (
            <AuditEvidencePanel />
          ) : (
            <>
              <header>
                <h1 className="text-[24px] font-semibold tracking-tight">
                  账户与安全
                </h1>
                <p className="mt-1 text-[13px] text-slate-500">
                  管理 Echo OS 设备密码和已登录会话
                </p>
              </header>

              <section className="mt-6 overflow-hidden rounded-2xl border border-slate-200/90 bg-white shadow-sm">
                <div className="flex gap-4 border-b border-slate-100 p-5">
                  <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-blue-50 text-blue-600">
                    <KeyRoundIcon className="size-5" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <h2 className="text-[15px] font-semibold">
                      更改管理员密码
                    </h2>
                    <p className="mt-1 text-xs leading-5 text-slate-500">
                      更新后会退出全部设备，旧密码、旧会话和未使用的操作授权立即失效。
                    </p>
                  </div>
                </div>
                <form
                  className="space-y-3 p-5"
                  onSubmit={(event) => {
                    event.preventDefault();
                    if (!busy) void rotate();
                  }}
                >
                  <label className="grid grid-cols-[118px_1fr] items-center gap-3 text-[13px] text-slate-600">
                    <span>当前密码</span>
                    <input
                      id={currentPasswordId}
                      type="password"
                      autoComplete="current-password"
                      value={currentPassword}
                      disabled={busy}
                      onChange={(event) =>
                        setCurrentPassword(event.currentTarget.value)
                      }
                      className="h-9 rounded-lg border border-slate-300 bg-white px-3 text-slate-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500/15"
                    />
                  </label>
                  <label className="grid grid-cols-[118px_1fr] items-center gap-3 text-[13px] text-slate-600">
                    <span>新密码</span>
                    <input
                      id={newPasswordId}
                      type="password"
                      autoComplete="new-password"
                      value={newPassword}
                      disabled={busy}
                      onChange={(event) =>
                        setNewPassword(event.currentTarget.value)
                      }
                      className="h-9 rounded-lg border border-slate-300 bg-white px-3 text-slate-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500/15"
                    />
                  </label>
                  <label className="grid grid-cols-[118px_1fr] items-center gap-3 text-[13px] text-slate-600">
                    <span>确认新密码</span>
                    <input
                      id={confirmationId}
                      type="password"
                      autoComplete="new-password"
                      value={confirmation}
                      disabled={busy}
                      onChange={(event) =>
                        setConfirmation(event.currentTarget.value)
                      }
                      className="h-9 rounded-lg border border-slate-300 bg-white px-3 text-slate-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500/15"
                    />
                  </label>
                  {error && (
                    <p role="alert" className="ml-[131px] text-xs text-red-600">
                      {error}
                    </p>
                  )}
                  <div className="flex justify-end pt-1">
                    <button
                      type="submit"
                      disabled={
                        busy ||
                        !currentPassword ||
                        !newPassword ||
                        !confirmation
                      }
                      className="inline-flex h-9 min-w-28 items-center justify-center gap-1.5 rounded-lg bg-blue-600 px-4 text-[13px] font-medium text-white transition hover:bg-blue-700 disabled:opacity-45"
                    >
                      {busy && (
                        <Loader2Icon className="size-3.5 animate-spin" />
                      )}
                      {busy ? "正在更新…" : "更新密码"}
                    </button>
                  </div>
                </form>
              </section>

              <section className="mt-4 overflow-hidden rounded-2xl border border-slate-200/90 bg-white shadow-sm">
                <div className="flex gap-4 border-b border-slate-100 p-5">
                  <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-emerald-50 text-emerald-600">
                    <ShieldCheckIcon className="size-5" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <h2 className="text-[15px] font-semibold">
                      管理员动态验证码
                    </h2>
                    <p className="mt-1 text-xs leading-5 text-slate-500">
                      {totpStatus?.enabled
                        ? `已启用；还可使用 ${totpStatus.recoveryCodesRemaining} 个一次性恢复码。`
                        : "使用兼容 TOTP 的验证器，为管理员密码增加第二道登录保护。"}
                    </p>
                  </div>
                </div>

                <div className="space-y-3 p-5">
                  {totpEnrollment ? (
                    <>
                      <p className="text-xs leading-5 text-slate-600">
                        在验证器中扫描/打开下方
                        URI，或手动输入密钥。先离线保存恢复码，再输入 6
                        位验证码完成启用。
                      </p>
                      <div className="rounded-xl bg-slate-50 p-3 font-mono text-[11px] text-slate-700 ring-1 ring-slate-200">
                        <p className="break-all">
                          密钥：{totpEnrollment.secret}
                        </p>
                        <p className="mt-2 break-all text-slate-500">
                          {totpEnrollment.otpauthUri}
                        </p>
                      </div>
                      <div>
                        <strong className="text-xs text-slate-700">
                          一次性恢复码（离开后不再显示）
                        </strong>
                        <div className="mt-2 grid grid-cols-2 gap-1 rounded-xl bg-amber-50 p-3 font-mono text-xs text-amber-950 ring-1 ring-amber-200">
                          {totpEnrollment.recoveryCodes.map((code) => (
                            <span key={code}>{code}</span>
                          ))}
                        </div>
                      </div>
                      <label className="grid grid-cols-[118px_1fr] items-center gap-3 text-[13px] text-slate-600">
                        <span>6 位验证码</span>
                        <input
                          type="text"
                          inputMode="numeric"
                          autoComplete="one-time-code"
                          aria-label="6 位动态验证码"
                          maxLength={6}
                          value={totpFactor}
                          disabled={totpBusy}
                          onChange={(event) =>
                            setTotpFactor(event.currentTarget.value)
                          }
                          className="h-9 rounded-lg border border-slate-300 bg-white px-3 font-mono text-slate-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500/15"
                        />
                      </label>
                      <div className="flex justify-end">
                        <button
                          type="button"
                          disabled={totpBusy || !/^\d{6}$/.test(totpFactor)}
                          onClick={() => void confirmTotp()}
                          className="h-9 rounded-lg bg-emerald-600 px-4 text-[13px] font-medium text-white transition hover:bg-emerald-700 disabled:opacity-45"
                        >
                          启用并退出旧会话
                        </button>
                      </div>
                    </>
                  ) : totpStatus?.enabled ? (
                    <>
                      <label className="grid grid-cols-[118px_1fr] items-center gap-3 text-[13px] text-slate-600">
                        <span>管理员密码</span>
                        <input
                          type="password"
                          autoComplete="current-password"
                          aria-label="关闭动态验证码的管理员密码"
                          value={totpPassword}
                          disabled={totpBusy}
                          onChange={(event) =>
                            setTotpPassword(event.currentTarget.value)
                          }
                          className="h-9 rounded-lg border border-slate-300 bg-white px-3 text-slate-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500/15"
                        />
                      </label>
                      <label className="grid grid-cols-[118px_1fr] items-center gap-3 text-[13px] text-slate-600">
                        <span>验证码/恢复码</span>
                        <input
                          type="text"
                          autoComplete="one-time-code"
                          aria-label="关闭动态验证码的验证码或恢复码"
                          value={totpFactor}
                          disabled={totpBusy}
                          onChange={(event) =>
                            setTotpFactor(event.currentTarget.value)
                          }
                          className="h-9 rounded-lg border border-slate-300 bg-white px-3 font-mono text-slate-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500/15"
                        />
                      </label>
                      <div className="flex justify-end">
                        <button
                          type="button"
                          disabled={
                            totpBusy || !totpPassword || !totpFactor.trim()
                          }
                          onClick={() => void disableTotp()}
                          className="h-9 rounded-lg border border-red-200 bg-red-50 px-4 text-[13px] font-medium text-red-700 transition hover:bg-red-100 disabled:opacity-45"
                        >
                          关闭动态验证码…
                        </button>
                      </div>
                    </>
                  ) : totpStatus ? (
                    <>
                      <label className="grid grid-cols-[118px_1fr] items-center gap-3 text-[13px] text-slate-600">
                        <span>管理员密码</span>
                        <input
                          type="password"
                          autoComplete="current-password"
                          aria-label="启用动态验证码的管理员密码"
                          value={totpPassword}
                          disabled={totpBusy}
                          onChange={(event) =>
                            setTotpPassword(event.currentTarget.value)
                          }
                          className="h-9 rounded-lg border border-slate-300 bg-white px-3 text-slate-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500/15"
                        />
                      </label>
                      <div className="flex justify-end">
                        <button
                          type="button"
                          disabled={totpBusy || !totpPassword}
                          onClick={() => void beginTotp()}
                          className="h-9 rounded-lg bg-blue-600 px-4 text-[13px] font-medium text-white transition hover:bg-blue-700 disabled:opacity-45"
                        >
                          设置动态验证码…
                        </button>
                      </div>
                    </>
                  ) : !totpError ? (
                    <p className="flex items-center gap-2 text-xs text-slate-500">
                      <Loader2Icon className="size-3.5 animate-spin" />
                      正在读取安全状态…
                    </p>
                  ) : null}
                  {totpError && (
                    <p role="alert" className="text-xs text-red-600">
                      {totpError}
                    </p>
                  )}
                </div>
              </section>

              <section className="mt-4 flex items-center gap-4 rounded-2xl border border-slate-200/90 bg-white p-5 shadow-sm">
                <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-red-50 text-red-600">
                  <LogOutIcon className="size-5" />
                </span>
                <div className="min-w-0 flex-1">
                  <h2 className="text-[15px] font-semibold">退出所有登录</h2>
                  <p className="mt-1 text-xs leading-5 text-slate-500">
                    让浏览器、Agent 工作台和实时连接中的现有登录全部失效。
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setRevokeOpen(true)}
                  className="h-9 shrink-0 rounded-lg border border-red-200 bg-red-50 px-3.5 text-[13px] font-medium text-red-700 transition hover:bg-red-100"
                >
                  全部退出…
                </button>
              </section>

              <p className="mt-4 flex items-center gap-1.5 text-[11px] text-slate-400">
                <LockKeyholeIcon className="size-3.5" />
                密码只发送到当前 Echo OS 设备，不会保存在浏览器中。
              </p>
            </>
          )}
        </main>
      </section>

      <HighRiskApprovalDialog
        open={revokeOpen}
        title="退出所有设备？"
        description="所有浏览器、Agent 工作台和实时连接都需要重新登录。"
        targetLabel="设备管理员：admin"
        confirmLabel="全部退出"
        destructive
        onCancel={() => setRevokeOpen(false)}
        onConfirm={revoke}
      />
    </div>
  );
}
