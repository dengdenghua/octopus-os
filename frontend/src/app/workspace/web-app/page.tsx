import { useEffect, useMemo, useRef, useState } from "react";
import { ExternalLinkIcon, GlobeIcon, RefreshCwIcon } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import {
  WebviewTab,
  type WebviewTabHandle,
} from "@/components/browser/webview-tab";
import type { BrowserTab } from "@/components/browser/browser-store";

function safeWebUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? parsed.toString()
      : null;
  } catch {
    return null;
  }
}

export default function WorkspaceWebAppPage() {
  const [searchParams] = useSearchParams();
  const requestedUrl = safeWebUrl(searchParams.get("url"));
  const requestedTitle = searchParams.get("title")?.trim() || "网页应用";
  const handleRef = useRef<WebviewTabHandle | null>(null);
  const initialTab = useMemo<BrowserTab>(
    () => ({
      id: `workspace-web:${requestedUrl ?? "invalid"}`,
      url: requestedUrl ?? "about:blank",
      title: requestedTitle,
      isLoading: Boolean(requestedUrl),
      device: "desktop",
    }),
    [requestedTitle, requestedUrl],
  );
  const [tab, setTab] = useState(initialTab);

  useEffect(() => setTab(initialTab), [initialTab]);

  if (!requestedUrl) {
    return (
      <div className="grid h-full min-h-[280px] place-items-center bg-background px-6 text-center">
        <div>
          <GlobeIcon className="mx-auto size-8 text-muted-foreground" />
          <div className="mt-3 text-sm font-semibold">无法打开网页应用</div>
          <div className="mt-1 text-xs text-muted-foreground">
            当前地址无效或不是安全的网页地址。
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 w-full flex-col bg-background">
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border-subtle bg-background/96 px-3">
        <div className="grid size-7 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
          <GlobeIcon className="size-4" />
        </div>
        <div className="hidden min-w-0 max-w-44 sm:block">
          <div className="truncate text-xs font-semibold">{tab.title}</div>
        </div>
        <div className="min-w-0 flex-1 truncate rounded-lg bg-muted/45 px-3 py-1.5 text-xs text-muted-foreground">
          {tab.url}
        </div>
        <button
          type="button"
          onClick={() => handleRef.current?.reload()}
          className="grid size-8 shrink-0 place-items-center rounded-lg text-muted-foreground transition hover:bg-muted hover:text-foreground"
          aria-label="刷新网页"
          title="刷新网页"
        >
          <RefreshCwIcon className="size-4" />
        </button>
        <a
          href={tab.url}
          target="_blank"
          rel="noreferrer"
          className="grid size-8 shrink-0 place-items-center rounded-lg text-muted-foreground transition hover:bg-muted hover:text-foreground"
          aria-label="在新窗口打开"
          title="在新窗口打开"
        >
          <ExternalLinkIcon className="size-4" />
        </a>
      </header>
      <div className="relative min-h-0 flex-1 overflow-hidden">
        <WebviewTab
          key={initialTab.id}
          ref={handleRef}
          tab={tab}
          active
          renderDevice="desktop"
          onPatch={(patch) => setTab((current) => ({ ...current, ...patch }))}
        />
      </div>
    </div>
  );
}
