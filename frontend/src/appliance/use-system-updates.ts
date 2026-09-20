/**
 * 系统更新 hook。
 *
 * 封装 DesktopShellPage 里的 window.echo.updates 交互：
 * capabilities + status 轮询（仅在更新面板打开时）、apply 安装动作。
 */

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import type {
  SystemUpdateCapabilities,
  SystemUpdateStatus,
} from "@/types/electron";

export function useSystemUpdates(options: {
  /** 更新面板（关于/设置-通用）是否打开；打开时启用轮询。 */
  surfaceOpen: boolean;
}) {
  const [capabilities, setCapabilities] =
    useState<SystemUpdateCapabilities | null>(null);
  const [status, setStatus] = useState<SystemUpdateStatus | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const updates = window.echo?.updates;
    if (!updates) {
      setCapabilities({
        nativeShell: false,
        status: false,
        apply: false,
        reason: "system updates require the native Linux session shell",
      });
      setStatus({
        schema: 1,
        state: "unavailable",
        error: "请在 Echo OS 原生 Linux 桌面中查看系统更新。",
      });
      return;
    }
    try {
      const [nextCapabilities, nextStatus] = await Promise.all([
        updates.getCapabilities(),
        updates.getStatus(),
      ]);
      setCapabilities(nextCapabilities);
      setStatus(nextStatus);
    } catch (error) {
      setStatus({
        schema: 1,
        state: "unavailable",
        error: error instanceof Error ? error.message : "系统更新状态读取失败",
      });
    }
  }, []);

  useEffect(() => {
    if (!options.surfaceOpen) return;
    void refresh();
    if (!busy && status?.state !== "checking" && status?.state !== "installing") {
      return;
    }
    const timer = window.setInterval(() => {
      void refresh();
    }, 1_000);
    return () => window.clearInterval(timer);
  }, [options.surfaceOpen, refresh, busy, status?.state]);

  const apply = useCallback(async () => {
    const updates = window.echo?.updates;
    if (!updates || busy) return;
    setBusy(true);
    try {
      const result = await updates.apply();
      if (!result.ok) {
        if (!result.cancelled) toast.error(result.error || "系统更新安装失败");
        return;
      }
      toast.success("系统更新已写入备用槽，重新启动后生效");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "系统更新安装失败");
    } finally {
      await refresh();
      setBusy(false);
    }
  }, [busy, refresh]);

  return { capabilities, status, busy, refresh, apply };
}
