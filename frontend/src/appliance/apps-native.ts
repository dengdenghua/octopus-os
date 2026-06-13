/**
 * 原生 shell(A 路线):本地已装应用(freedesktop .desktop)。
 *
 * 仅 Electron 会话 shell 模式有(`window.octopus.apps`);web/浏览器端 → 空数组,
 * Dock 不显示原生应用,行为不变。图标用主进程读好的 data URL 直接显示。
 */

import { useCallback, useEffect, useState } from "react";

import type { NativeApp } from "@/types/electron";

export function useNativeApps(): {
  apps: NativeApp[];
  launch: (exec: string) => void;
} {
  const [apps, setApps] = useState<NativeApp[]>([]);

  useEffect(() => {
    const api = window.octopus?.apps;
    if (!api) return; // 非原生 shell(web / 寄生窗口)→ 无原生应用
    let alive = true;
    api
      .list()
      .then((list) => {
        if (alive) setApps(Array.isArray(list) ? list : []);
      })
      .catch(() => {
        if (alive) setApps([]);
      });
    return () => {
      alive = false;
    };
  }, []);

  const launch = useCallback((exec: string) => {
    void window.octopus?.apps?.launch(exec).catch(() => {});
  }, []);

  return { apps, launch };
}
