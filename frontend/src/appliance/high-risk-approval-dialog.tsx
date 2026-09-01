import { useEffect, useId, useRef, useState } from "react";
import {
  AlertTriangleIcon,
  Loader2Icon,
  LockKeyholeIcon,
  ShieldCheckIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

export function HighRiskApprovalDialog({
  open,
  title,
  description,
  targetLabel,
  confirmLabel = "确认执行",
  destructive = false,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  description: string;
  targetLabel?: string;
  confirmLabel?: string;
  destructive?: boolean;
  onCancel: () => void;
  onConfirm: (password: string) => Promise<void>;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const passwordId = useId();
  const passwordRef = useRef<HTMLInputElement>(null);
  const busyRef = useRef(false);
  const onCancelRef = useRef(onCancel);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  busyRef.current = busy;
  onCancelRef.current = onCancel;

  useEffect(() => {
    if (!open) return;
    setPassword("");
    setError(null);
    const focusTimer = window.setTimeout(
      () => passwordRef.current?.focus(),
      50,
    );
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busyRef.current) onCancelRef.current();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.clearTimeout(focusTimer);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[140] grid place-items-center bg-slate-950/38 p-5 backdrop-blur-[7px]"
      data-desktop-interactive
    >
      <button
        type="button"
        className="absolute inset-0 cursor-default"
        aria-label="取消高风险操作"
        onClick={() => !busy && onCancel()}
      />
      <section
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        className="relative w-full max-w-[430px] overflow-hidden rounded-[22px] border border-white/75 bg-white/95 text-slate-900 shadow-[0_28px_90px_rgba(15,23,42,0.35)]"
      >
        <div className="flex items-start gap-3.5 px-5 pb-3 pt-5">
          <div
            className={cn(
              "grid size-11 shrink-0 place-items-center rounded-2xl",
              destructive
                ? "bg-red-50 text-red-600"
                : "bg-amber-50 text-amber-600",
            )}
          >
            {destructive ? (
              <AlertTriangleIcon className="size-5" />
            ) : (
              <ShieldCheckIcon className="size-5" />
            )}
          </div>
          <div className="min-w-0 pt-0.5">
            <h2
              id={titleId}
              className="text-[17px] font-semibold tracking-tight"
            >
              {title}
            </h2>
            <p
              id={descriptionId}
              className="mt-1 text-[13px] leading-5 text-slate-500"
            >
              {description}
            </p>
            {targetLabel && (
              <p className="mt-2 truncate rounded-lg bg-slate-100 px-2.5 py-1.5 font-mono text-[11px] text-slate-600">
                {targetLabel}
              </p>
            )}
          </div>
        </div>

        <form
          className="px-5 pb-5"
          onSubmit={async (event) => {
            event.preventDefault();
            if (!password || busy) return;
            setBusy(true);
            setError(null);
            try {
              await onConfirm(password);
              setPassword("");
            } catch (reason) {
              setError(
                reason instanceof Error ? reason.message : "管理员复核失败",
              );
              passwordRef.current?.select();
            } finally {
              setBusy(false);
            }
          }}
        >
          <label
            htmlFor={passwordId}
            className="mb-1.5 block text-xs font-medium text-slate-600"
          >
            设备管理员密码
          </label>
          <div className="relative">
            <LockKeyholeIcon className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
            <input
              id={passwordId}
              ref={passwordRef}
              type="password"
              autoComplete="current-password"
              value={password}
              disabled={busy}
              onChange={(event) => setPassword(event.currentTarget.value)}
              className="h-10 w-full rounded-xl border border-slate-300 bg-white pl-9 pr-3 text-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500/15 disabled:opacity-60"
              placeholder="输入密码以证明是你本人"
            />
          </div>
          <p className="mt-2 text-[11px] leading-4 text-slate-400">
            本次授权只用于上方操作，短时有效且使用一次后立即作废。
          </p>
          {error && (
            <p
              role="alert"
              className="mt-2 rounded-lg bg-red-50 px-2.5 py-2 text-xs text-red-700"
            >
              {error}
            </p>
          )}
          <div className="mt-4 flex justify-end gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={onCancel}
              className="h-9 rounded-xl border border-slate-300 bg-white px-4 text-sm font-medium text-slate-600 transition hover:bg-slate-50 disabled:opacity-50"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={!password || busy}
              className={cn(
                "inline-flex h-9 min-w-24 items-center justify-center gap-1.5 rounded-xl px-4 text-sm font-medium text-white transition disabled:opacity-50",
                destructive
                  ? "bg-red-600 hover:bg-red-700"
                  : "bg-blue-600 hover:bg-blue-700",
              )}
            >
              {busy && <Loader2Icon className="size-3.5 animate-spin" />}
              {busy ? "正在复核…" : confirmLabel}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
