/**
 * Echo OS appliance 登录屏(原生路线)。
 *
 * 与桌面同一极光壁纸 + 毛玻璃卡片；管理员与家庭成员使用各自账号。
 * 登录成功后回调 onSuccess,由桌面切换到主界面。
 */

import { useEffect, useState, type FormEvent } from "react";
import {
  ArrowRightIcon,
  Loader2Icon,
  MoonIcon,
  PowerIcon,
  RotateCcwIcon,
  WifiIcon,
} from "lucide-react";

import {
  ApplianceSecondFactorRequiredError,
  applianceLogin,
} from "@/appliance/auth";
import { EchoMark } from "@/components/brand/echo-mark";
import type {
  MacSystemAction,
  MacSystemCapabilities,
} from "@/appliance/macos-shell";
import { MacDesktopWallpaperArtwork } from "@/appliance/macos-shell";

export function ApplianceLogin({
  onSuccess,
  systemCapabilities,
  onSystemAction,
}: {
  onSuccess: () => void;
  systemCapabilities: MacSystemCapabilities;
  onSystemAction: (action: MacSystemAction) => void;
}) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [secondFactor, setSecondFactor] = useState("");
  const [secondFactorRequired, setSecondFactorRequired] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const submittedUsername = String(formData.get("username") ?? "").trim();
    const submittedPassword = String(formData.get("password") ?? "");
    const submittedSecondFactor = String(
      formData.get("secondFactor") ?? "",
    ).trim();
    if (
      !submittedUsername ||
      !submittedPassword ||
      (secondFactorRequired && !submittedSecondFactor) ||
      submitting
    )
      return;
    // Password managers may populate native inputs without dispatching the
    // change event React uses for controlled state. Keep the submitted DOM
    // values before the loading render so two-factor login retains them.
    setUsername(submittedUsername);
    setPassword(submittedPassword);
    setSecondFactor(submittedSecondFactor);
    setSubmitting(true);
    setError(null);
    try {
      await applianceLogin(
        submittedUsername,
        submittedPassword,
        submittedSecondFactor || undefined,
      );
      onSuccess();
    } catch (err) {
      if (err instanceof ApplianceSecondFactorRequiredError) {
        setSecondFactorRequired(true);
        setError(null);
        setSubmitting(false);
        return;
      }
      setError(err instanceof Error ? err.message : "登录失败");
      setSubmitting(false);
    }
  };

  return (
    <main className="macos-desktop-root mac-login-screen relative h-screen overflow-hidden bg-transparent text-white">
      <div aria-hidden className="desktop-wallpaper absolute inset-0 z-0">
        <MacDesktopWallpaperArtwork />
        <span className="desktop-wallpaper-fold desktop-wallpaper-fold-a" />
        <span className="desktop-wallpaper-fold desktop-wallpaper-fold-b" />
        <span className="desktop-wallpaper-fold desktop-wallpaper-fold-c" />
      </div>
      <div className="mac-login-vignette" />
      <time className="mac-login-clock">
        <span>
          {now.toLocaleTimeString("zh-CN", {
            hour: "2-digit",
            minute: "2-digit",
            hour12: false,
          })}
        </span>
        <small>
          {now.getMonth() + 1}月{now.getDate()}日 周
          {"日一二三四五六"[now.getDay()]}
        </small>
      </time>
      <form onSubmit={submit} className="mac-login-form">
        <div className="mac-login-avatar">
          <EchoMark tone="light" />
        </div>
        <h1>Echo</h1>
        <p>{username.trim() === "admin" ? "设备管理员" : "家庭成员"}</p>

        <label className="mac-login-username">
          <input
            name="username"
            type="text"
            autoFocus
            autoComplete="username"
            required
            spellCheck={false}
            value={username}
            onChange={(event) => {
              setUsername(event.target.value);
              setSecondFactorRequired(false);
              setSecondFactor("");
              if (error) setError(null);
            }}
            placeholder="用户名"
            aria-label="用户名"
          />
        </label>

        {secondFactorRequired && (
          <label className="mac-login-password">
            <input
              name="secondFactor"
              type="text"
              inputMode="numeric"
              autoFocus
              autoComplete="one-time-code"
              required
              spellCheck={false}
              value={secondFactor}
              onChange={(event) => {
                setSecondFactor(event.target.value);
                if (error) setError(null);
              }}
              placeholder="动态验证码或恢复码"
              aria-label="动态验证码或恢复码"
            />
            <button
              type="submit"
              disabled={submitting}
              aria-label="验证并进入桌面"
            >
              {submitting ? (
                <Loader2Icon className="animate-spin" />
              ) : (
                <ArrowRightIcon />
              )}
            </button>
          </label>
        )}

        <label className="mac-login-password">
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
              if (error) setError(null);
            }}
            placeholder="输入密码"
            aria-label="密码"
          />
          {!secondFactorRequired && (
            <button type="submit" disabled={submitting} aria-label="进入桌面">
              {submitting ? (
                <Loader2Icon className="animate-spin" />
              ) : (
                <ArrowRightIcon />
              )}
            </button>
          )}
        </label>

        {error && <p className="mac-login-error">{error}</p>}
        <small>使用 Echo 家庭账号登录</small>
      </form>

      <div className="mac-login-system-actions">
        <button
          type="button"
          title="睡眠"
          disabled={!systemCapabilities.suspend}
          onClick={() => onSystemAction("suspend")}
        >
          <span>
            <MoonIcon />
          </span>
          <small>睡眠</small>
        </button>
        <button
          type="button"
          title="重新启动"
          disabled={!systemCapabilities.restart}
          onClick={() => onSystemAction("restart")}
        >
          <span>
            <RotateCcwIcon />
          </span>
          <small>重新启动</small>
        </button>
        <button
          type="button"
          title="关机"
          disabled={!systemCapabilities.shutdown}
          onClick={() => onSystemAction("shutdown")}
        >
          <span>
            <PowerIcon />
          </span>
          <small>关机</small>
        </button>
      </div>

      <footer className="mac-login-footer">
        <span>
          <WifiIcon />
          Echo Home
        </span>
        <span>首次启动密码可在设备控制台中查看</span>
      </footer>
    </main>
  );
}

export function ApplianceSessionGate({
  state,
  onRetry,
}: {
  state: "checking" | "starting" | "unavailable";
  onRetry?: () => void;
}) {
  const unavailable = state === "unavailable";
  const heading = unavailable
    ? "暂时无法连接系统服务"
    : state === "starting"
      ? "系统服务正在启动"
      : "正在确认设备会话";
  const detail = unavailable
    ? "无法安全确认当前设备会话。请检查服务状态后重试。"
    : state === "starting"
      ? "启动完成后将自动进入登录界面，请稍候。"
      : "正在连接 Echo OS，请稍候。";

  return (
    <main
      className="macos-desktop-root mac-login-screen relative h-screen overflow-hidden bg-transparent text-white"
      aria-busy={!unavailable}
    >
      <div aria-hidden className="desktop-wallpaper absolute inset-0 z-0">
        <MacDesktopWallpaperArtwork />
        <span className="desktop-wallpaper-fold desktop-wallpaper-fold-a" />
        <span className="desktop-wallpaper-fold desktop-wallpaper-fold-b" />
        <span className="desktop-wallpaper-fold desktop-wallpaper-fold-c" />
      </div>
      <div className="mac-login-vignette" />
      <section
        className="mac-login-form"
        role={unavailable ? "alert" : "status"}
      >
        <div className="mac-login-avatar">
          {unavailable ? (
            <EchoMark tone="light" />
          ) : (
            <Loader2Icon className="animate-spin" aria-hidden />
          )}
        </div>
        <h1>{heading}</h1>
        <p>{detail}</p>
        {unavailable && onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            className="mt-3 rounded-full border border-white/30 bg-white/15 px-5 py-2 text-sm font-medium text-white transition hover:bg-white/25 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
          >
            重试连接
          </button>
        ) : null}
      </section>
      <footer className="mac-login-footer">
        <span>
          <WifiIcon />
          Echo Home
        </span>
        <span>设备会话采用失败关闭保护</span>
      </footer>
    </main>
  );
}
