"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ExternalLinkIcon } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import {
  WorkspaceBody,
  WorkspaceContainer,
} from "@/components/workspace/workspace-container";
import { useI18n } from "@/core/i18n/hooks";
import { getToken } from "@/core/auth/api";
import { getBackendBaseURL, getQuoteHubBaseURL } from "@/core/config";
import { useAuth } from "@/providers/AuthProvider";
import { toHashRouterShellUrl } from "@/core/router/hash-shell-url";

type Tab = "platform" | "watch";

export function isTrustedQuoteFrameOrigin(
  origin: string,
  currentOrigin: string,
): boolean {
  if (origin === currentOrigin) return true;
  try {
    const url = new URL(origin);
    if (
      (url.protocol === "http:" || url.protocol === "https:") &&
      (url.hostname === "127.0.0.1" || url.hostname === "localhost")
    ) {
      return true;
    }
  } catch {
    return false;
  }
  return (
    origin === "https://api.echo-age.com" ||
    origin === "https://ai.echo-age.com" ||
    origin === "https://os.echo-age.com"
  );
}

/**
 * 模拟炒股(paper_trading)插件页 —— 侧边栏入口。
 *
 * 插件本身是独立 HTML(由后端路由提供),这里用同源 iframe 嵌入到工作台,
 * 复用后端路由 / 凭证,不重复实现前端。两个 tab:
 *  - 平台原版:平台完整交易界面(行情/自选/持仓/下单)
 *  - 盯盘:紧凑真实行情面板(大盘+持仓+自选,自动刷新+涨跌提醒)
 */
export default function PaperTradingPage() {
  const { t } = useI18n();
  const { refresh } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [tab, setTab] = useState<Tab>(() =>
    searchParams.get("tab") === "watch" ? "watch" : "platform",
  );
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const refreshInFlight = useRef<Promise<void> | null>(null);
  const pluginPath =
    tab === "watch"
      ? "/api/plugins/paper-trading/watch"
      : "/api/plugins/paper-trading/page";
  const src = `${getBackendBaseURL()}${pluginPath}`;
  const openUrl =
    tab === "watch"
      ? toHashRouterShellUrl("/workspace/paper-trading?tab=watch")
      : src;

  const selectTab = useCallback(
    (next: Tab) => {
      setTab(next);
      const params = new URLSearchParams(searchParams);
      if (next === "watch") params.set("tab", "watch");
      else params.delete("tab");
      setSearchParams(params, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  useEffect(() => {
    const requested =
      searchParams.get("tab") === "watch" ? "watch" : "platform";
    setTab(requested);
  }, [searchParams]);

  const sendQuoteConfig = useCallback(() => {
    const frame = iframeRef.current;
    if (!frame?.contentWindow || typeof window === "undefined") return;
    let targetOrigin: string;
    try {
      targetOrigin = new URL(frame.src, window.location.href).origin;
    } catch {
      return;
    }
    if (!isTrustedQuoteFrameOrigin(targetOrigin, window.location.origin))
      return;
    frame.contentWindow.postMessage(
      {
        type: "echo:quote-config",
        version: 1,
        quoteBaseUrl: getQuoteHubBaseURL() || targetOrigin,
        bearer: getToken() || "",
      },
      targetOrigin,
    );
  }, []);

  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      const frame = iframeRef.current;
      if (!frame?.contentWindow || event.source !== frame.contentWindow) return;
      let expectedOrigin: string;
      try {
        expectedOrigin = new URL(frame.src, window.location.href).origin;
      } catch {
        return;
      }
      if (
        event.origin !== expectedOrigin ||
        !isTrustedQuoteFrameOrigin(expectedOrigin, window.location.origin)
      ) {
        return;
      }
      const message = event.data as { type?: unknown; reason?: unknown } | null;
      if (!message || message.type !== "echo:quote-config-request") return;
      const needsRefresh =
        message.reason === "reauth" || message.reason === "unauthorized";
      if (!needsRefresh) {
        sendQuoteConfig();
        return;
      }
      if (!refreshInFlight.current) {
        refreshInFlight.current = refresh()
          .catch(() => undefined)
          .then(() => undefined)
          .finally(() => {
            refreshInFlight.current = null;
            sendQuoteConfig();
          });
      }
    };
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [refresh, sendQuoteConfig]);

  return (
    <WorkspaceContainer className="!p-0 md:!px-0">
      <WorkspaceBody className="!p-0">
        <div className="flex h-full w-full min-h-0 flex-col items-stretch">
          <div className="flex h-11 shrink-0 items-center justify-between gap-3 border-b px-4">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <h1>🐟 模拟炒股</h1>
              <div className="ml-2 flex items-center rounded-lg border bg-muted/40 p-0.5">
                <button
                  type="button"
                  onClick={() => selectTab("platform")}
                  className={`rounded-md px-3 py-1 text-xs transition-colors ${
                    tab === "platform"
                      ? "bg-background font-semibold text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  平台原版
                </button>
                <button
                  type="button"
                  onClick={() => selectTab("watch")}
                  className={`rounded-md px-3 py-1 text-xs transition-colors ${
                    tab === "watch"
                      ? "bg-background font-semibold text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  📡 盯盘
                </button>
              </div>
              <span className="text-xs font-normal text-muted-foreground">
                {tab === "watch"
                  ? "真实行情 · 自动刷新 · 涨跌提醒"
                  : t.sidebar.navPaperTradingDesc}
              </span>
            </div>
            <Button
              variant="ghost"
              size="sm"
              asChild
              className="gap-1.5 text-xs text-muted-foreground"
            >
              <a href={openUrl} target="_blank" rel="noreferrer">
                <ExternalLinkIcon className="size-3.5" />
                新窗口打开
              </a>
            </Button>
          </div>
          <iframe
            ref={iframeRef}
            key={tab}
            src={src}
            onLoad={tab === "watch" ? sendQuoteConfig : undefined}
            title={tab === "watch" ? "盯盘" : t.sidebar.navPaperTrading}
            className="w-full flex-1 border-0"
          />
        </div>
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}
