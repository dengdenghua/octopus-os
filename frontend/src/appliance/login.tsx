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

import { applianceLogin } from "@/appliance/auth";
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
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!username.trim() || !password || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await applianceLogin(username, password);
      onSuccess();
    } catch (err) {
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
            type="text"
            autoFocus
            autoComplete="username"
            spellCheck={false}
            value={username}
            onChange={(event) => {
              setUsername(event.target.value);
              if (error) setError(null);
            }}
            placeholder="用户名"
            aria-label="用户名"
          />
        </label>

        <label className="mac-login-password">
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
              if (error) setError(null);
            }}
            placeholder="输入密码"
            aria-label="密码"
          />
          <button
            type="submit"
            disabled={!username.trim() || !password || submitting}
            aria-label="进入桌面"
          >
            {submitting ? (
              <Loader2Icon className="animate-spin" />
            ) : (
              <ArrowRightIcon />
            )}
          </button>
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
