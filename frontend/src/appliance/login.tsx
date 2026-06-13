/**
 * Octopus OS appliance 登录屏(原生路线)。
 *
 * 与桌面同一极光壁纸 + 毛玻璃卡片;单用户(admin)只需密码。
 * 登录成功后回调 onSuccess,由桌面切换到主界面。
 */

import { useState, type FormEvent } from "react";
import { LockIcon, Loader2Icon } from "lucide-react";

import { applianceLogin } from "@/appliance/auth";

export function ApplianceLogin({ onSuccess }: { onSuccess: () => void }) {
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!password || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await applianceLogin(password);
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
      setSubmitting(false);
    }
  };

  return (
    <main className="relative grid h-screen place-items-center overflow-hidden bg-transparent text-white">
      <div aria-hidden className="desktop-wallpaper absolute inset-0 z-0" />
      <form
        onSubmit={submit}
        className="relative z-10 w-[min(92vw,360px)] rounded-[26px] border border-white/30 bg-white/15 p-7 shadow-[0_24px_60px_-16px_rgba(0,0,0,0.55)] ring-1 ring-inset ring-white/25 backdrop-blur-2xl"
      >
        <div className="mb-5 flex flex-col items-center gap-3 text-center">
          <div className="grid size-14 place-items-center rounded-2xl bg-white/20 ring-1 ring-white/30">
            <LockIcon className="size-6" />
          </div>
          <div>
            <h1 className="text-lg font-semibold">Octopus OS</h1>
            <p className="mt-0.5 text-xs text-white/70">
              输入管理员密码以进入桌面
            </p>
          </div>
        </div>

        <input
          type="password"
          autoFocus
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="管理员密码"
          className="h-11 w-full rounded-xl border border-white/25 bg-white/15 px-4 text-sm text-white outline-none transition placeholder:text-white/50 focus:border-white/45 focus:bg-white/20"
        />

        {error && (
          <p className="mt-2 text-center text-xs text-rose-200">{error}</p>
        )}

        <button
          type="submit"
          disabled={!password || submitting}
          className="mt-4 inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-white/90 text-sm font-semibold text-slate-900 transition hover:bg-white disabled:opacity-50"
        >
          {submitting && <Loader2Icon className="size-4 animate-spin" />}
          {submitting ? "登录中…" : "进入桌面"}
        </button>

        <p className="mt-4 text-center text-[11px] leading-relaxed text-white/55">
          首次启动的初始密码见容器日志,或由 OCTOPUS_ADMIN_PASSWORD 指定。
        </p>
      </form>
    </main>
  );
}
