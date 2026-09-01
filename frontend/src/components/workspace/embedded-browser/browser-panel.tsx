/* Implementation note. */

import { useCallback, useRef, useState, type KeyboardEvent } from "react";
import { ArrowLeftIcon, ArrowRightIcon, RefreshCwIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/core/i18n/hooks";
import { useBrowserPanel } from "./browser-context";
import { WebviewRenderer, type WebviewHandle } from "./webview-renderer";

const isElectron = (): boolean =>
  typeof window !== "undefined" && !!window.echo?.isElectron;

export function BrowserPanel() {
  const { t } = useI18n();
  const { url, mode, setUrl } = useBrowserPanel();
  const [inputValue, setInputValue] = useState(url);
  const [key, setKey] = useState(0);
  const electron = isElectron();

  // Implementation note.
  // Implementation note.
  const historyRef = useRef<string[]>(url ? [url] : []);
  const historyIndexRef = useRef(url ? 0 : -1);

  const [webviewCanBack, setWebviewCanBack] = useState(false);
  const [webviewCanForward, setWebviewCanForward] = useState(false);
  const webviewHandle = useRef<WebviewHandle | null>(null);

  const canGoBack = electron ? webviewCanBack : historyIndexRef.current > 0;
  const canGoForward = electron
    ? webviewCanForward
    : historyIndexRef.current < historyRef.current.length - 1;

  const navigateTo = useCallback(
    (target: string) => {
      historyRef.current = historyRef.current.slice(
        0,
        historyIndexRef.current + 1,
      );
      historyRef.current.push(target);
      historyIndexRef.current = historyRef.current.length - 1;
      setUrl(target);
      setInputValue(target);
    },
    [setUrl],
  );

  const handleNavigate = useCallback(() => {
    let target = inputValue.trim();
    if (!target) return;
    if (!/^https?:\/\//i.test(target)) target = `https://${target}`;
    navigateTo(target);
  }, [inputValue, navigateTo]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Enter") handleNavigate();
    },
    [handleNavigate],
  );

  const handleRefresh = useCallback(() => {
    if (electron) webviewHandle.current?.reload();
    else setKey((k) => k + 1);
  }, [electron]);

  const goBack = useCallback(() => {
    if (!canGoBack) return;
    if (electron) {
      webviewHandle.current?.goBack();
      return;
    }
    historyIndexRef.current -= 1;
    const prev = historyRef.current[historyIndexRef.current] ?? "";
    setUrl(prev);
    setInputValue(prev);
  }, [canGoBack, electron, setUrl]);

  const goForward = useCallback(() => {
    if (!canGoForward) return;
    if (electron) {
      webviewHandle.current?.goForward();
      return;
    }
    historyIndexRef.current += 1;
    const next = historyRef.current[historyIndexRef.current] ?? "";
    setUrl(next);
    setInputValue(next);
  }, [canGoForward, electron, setUrl]);

  return (
    <div className="flex h-full flex-col overflow-hidden bg-background">
      {/* Simplified browser toolbar */}
      <div className="flex min-h-10 items-center gap-1.5 border-b border-border-subtle bg-muted/30 px-2.5 py-1.5">
        <Button
          variant="ghost"
          size="icon"
          onClick={goBack}
          disabled={!canGoBack}
          aria-label={t.browser.back}
          className="size-7 hover:bg-muted/80"
        >
          <ArrowLeftIcon className="size-3.5 text-muted-foreground" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={goForward}
          disabled={!canGoForward}
          aria-label={t.browser.forward}
          className="size-7 hover:bg-muted/80"
        >
          <ArrowRightIcon className="size-3.5 text-muted-foreground" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={handleRefresh}
          aria-label={t.browser.reload}
          className="size-7 hover:bg-muted/80"
        >
          <RefreshCwIcon className="size-3.5 text-muted-foreground" />
        </Button>
        <Input
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={handleNavigate}
          placeholder={t.browser.urlPlaceholder}
          aria-label={t.browser.urlPlaceholder}
          className="flex-1 h-7 rounded-md border-0 bg-background/60 px-2.5 text-xs shadow-none focus-visible:bg-background focus-visible:ring-1 focus-visible:ring-border-default"
        />
      </div>

      {/* Implementation note. */}
      <div className="flex flex-1 overflow-hidden">
        {url ? (
          electron ? (
            <WebviewRenderer
              ref={webviewHandle}
              url={url}
              mode={mode}
              refreshKey={key}
              onCanGoBackChange={setWebviewCanBack}
              onCanGoForwardChange={setWebviewCanForward}
            />
          ) : (
            <iframe
              key={key}
              src={url}
              title={t.browser.startBrowsingHint}
              className="h-full w-full border-0"
              sandbox="allow-scripts allow-forms allow-popups"
            />
          )
        ) : (
          <div className="flex h-full w-full items-center justify-center text-muted-foreground">
            <p>{t.browser.startBrowsingHint}</p>
          </div>
        )}
      </div>
    </div>
  );
}
