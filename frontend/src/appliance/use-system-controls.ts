/**
 * 系统电源控制 hook（锁屏/注销/挂起/重启/关机）。
 *
 * 封装 DesktopShellPage 里的系统动作状态机：
 * - capabilities 轮询（window.echo.system.getCapabilities）
 * - 请求-确认对话（pendingSystemAction → confirmSystemAction）
 * - 锁屏直达（无需确认）
 * - systemControls 状态（控制中心开关面板的读写）
 */

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import type {
  SystemActionCapabilities,
  SystemControlState,
} from "@/types/electron";
import type { MacSystemAction, MacSystemCapabilities } from "./macos-shell";

const NO_SYSTEM_CAPABILITIES: SystemActionCapabilities = {
  nativeShell: false,
  lock: false,
  logout: false,
  suspend: false,
  restart: false,
  shutdown: false,
};

export function useSystemControls(options?: {
  /** 执行锁屏等动作前关闭浮层的回调（spotlight/launchpad 等）。 */
  closeTransientPanels?: () => void;
}) {
  const [systemCapabilities, setSystemCapabilities] =
    useState<SystemActionCapabilities>(NO_SYSTEM_CAPABILITIES);
  const [systemControls, setSystemControls] =
    useState<SystemControlState | null>(null);
  const [pendingSystemAction, setPendingSystemAction] =
    useState<MacSystemAction | null>(null);
  const [systemActionBusy, setSystemActionBusy] = useState(false);
  const [systemActionError, setSystemActionError] = useState<string | null>(
    null,
  );

  const refreshSystemControls = useCallback(async () => {
    const controls = window.echo?.systemControls;
    if (!controls) return;
    try {
      setSystemControls(await controls.getState());
    } catch (error) {
      console.warn("[echo] native system-control refresh failed", error);
    }
  }, []);

  useEffect(() => {
    if (!window.echo?.systemControls) return;
    void refreshSystemControls();
    const timer = window.setInterval(() => {
      void refreshSystemControls();
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [refreshSystemControls]);

  useEffect(() => {
    let alive = true;
    const system = window.echo?.system;
    if (!system) return;
    system
      .getCapabilities()
      .then((capabilities) => {
        if (alive) setSystemCapabilities(capabilities);
      })
      .catch(() => {
        if (alive) setSystemCapabilities(NO_SYSTEM_CAPABILITIES);
      });
    return () => {
      alive = false;
    };
  }, []);

  const applySystemControl = useCallback(
    async (
      label: string,
      operation: () => Promise<{ ok: boolean; error?: string }>,
    ) => {
      try {
        const result = await operation();
        if (!result.ok) {
          toast.error(result.error || `${label}设置失败`);
          return;
        }
        await refreshSystemControls();
      } catch (error) {
        toast.error(error instanceof Error ? error.message : `${label}设置失败`);
      }
    },
    [refreshSystemControls],
  );

  const availableSystemActions: MacSystemCapabilities = {
    lock: systemCapabilities.lock,
    logout: systemCapabilities.logout,
    suspend: systemCapabilities.suspend,
    restart: systemCapabilities.restart,
    shutdown: systemCapabilities.shutdown,
  };

  const lockScreen = useCallback(async () => {
    if (!systemCapabilities.lock) return;
    options?.closeTransientPanels?.();
    const system = window.echo?.system;
    if (!system) {
      toast.error("当前不是 Echo OS 原生系统会话");
      return;
    }
    try {
      const result = await system.runAction("lock");
      if (!result.ok) toast.error(result.error || "系统锁屏失败");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "系统锁屏失败");
    }
  }, [systemCapabilities.lock, options]);

  const requestSystemAction = useCallback(
    (action: MacSystemAction) => {
      if (!systemCapabilities[action]) return;
      options?.closeTransientPanels?.();
      setSystemActionError(null);
      setPendingSystemAction(action);
    },
    [systemCapabilities, options],
  );

  const cancelSystemAction = useCallback(() => {
    if (systemActionBusy) return;
    setPendingSystemAction(null);
    setSystemActionError(null);
  }, [systemActionBusy]);

  const confirmSystemAction = useCallback(async () => {
    if (!pendingSystemAction || systemActionBusy) return;
    const system = window.echo?.system;
    if (!system) {
      setSystemActionError("当前不是 Echo OS 原生系统会话");
      return;
    }
    setSystemActionBusy(true);
    setSystemActionError(null);
    try {
      const result = await system.runAction(pendingSystemAction);
      if (!result.ok) {
        setSystemActionError(result.error || "系统动作执行失败");
        return;
      }
      setPendingSystemAction(null);
    } catch (error) {
      setSystemActionError(
        error instanceof Error ? error.message : "系统动作执行失败",
      );
    } finally {
      setSystemActionBusy(false);
    }
  }, [pendingSystemAction, systemActionBusy]);

  return {
    systemCapabilities,
    systemControls,
    availableSystemActions,
    pendingSystemAction,
    systemActionBusy,
    systemActionError,
    applySystemControl,
    refreshSystemControls,
    lockScreen,
    requestSystemAction,
    cancelSystemAction,
    confirmSystemAction,
  };
}
