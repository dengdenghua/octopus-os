/**
 * 探测后端是否挂载了人类项目管理(company / PM)。
 *
 * OS appliance 形态默认剥离 company(PM 交给企业版插件),此时
 * /api/company/* 返回 404 → 前端据此隐藏"工作"surface 入口,避免点进
 * 失效页面。母体/非 appliance(company 在)→ 探测得 200/401 → 正常显示。
 *
 * 默认 true(探明 404 前不隐藏),网络错误也按可用处理——不误伤母体。
 * 模块级缓存,每会话只探一次。
 */

import { useEffect, useState } from "react";

import { authHeader } from "@/appliance/auth";

let cached: boolean | null = null;
let inflight: Promise<boolean> | null = null;

function probe(): Promise<boolean> {
  if (cached !== null) return Promise.resolve(cached);
  if (!inflight) {
    inflight = fetch("/api/company/projects", { headers: authHeader() })
      .then((r) => r.status !== 404) // 401/403/200 都算"已挂载"
      .catch(() => true) // 网络错误不隐藏
      .then((v) => {
        cached = v;
        return v;
      });
  }
  return inflight;
}

export function useCompanyEnabled(): boolean {
  const [enabled, setEnabled] = useState<boolean>(cached ?? true);
  useEffect(() => {
    let alive = true;
    void probe().then((v) => {
      if (alive) setEnabled(v);
    });
    return () => {
      alive = false;
    };
  }, []);
  return enabled;
}
