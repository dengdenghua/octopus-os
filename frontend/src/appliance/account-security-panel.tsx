import { useEffect, useId, useState } from "react";
import {
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
  revokeAllSessions,
  rotateAdminPassword,
} from "@/appliance/account-security";
import { AuditEvidencePanel } from "@/appliance/audit-evidence-panel";
import { requestHighRiskApproval } from "@/appliance/approval";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";
import { OmvStorageHealth } from "@/appliance/omv-storage-health";
import { OmvSharingPanel } from "@/appliance/omv-sharing-panel";

export type AccountSecuritySection =
  | "account"
  | "storage"
  | "sharing"
  | "audit";

export function AccountSecurityPanel({
  open,
  onClose,
  onSessionEnded,
  initialSection = "account",
}: {
  open: boolean;
  onClose: () => void;
  onSessionEnded: (message: string) => void;
  initialSection?: AccountSecuritySection;
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
  const [section, setSection] =
    useState<AccountSecuritySection>(initialSection);

  useEffect(() => {
    setCurrentPassword("");
    setNewPassword("");
    setConfirmation("");
    setError(null);
    setSection(initialSection);
  }, [initialSection, open]);

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
      <section className="relative flex h-[min(560px,calc(100vh-150px))] w-[min(820px,calc(100vw-40px))] overflow-hidden rounded-[22px] border border-white/70 bg-[#f4f4f5]/95 text-slate-900 shadow-[0_30px_90px_rgba(15,23,42,0.38)]">
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
          {section === "storage" ? (
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
