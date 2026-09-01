/* Implementation note. */

import { swallow } from "@/core/utils/log";
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type CSSProperties,
} from "react";

import type { DevicePreset } from "./browser-context";

interface Props {
  url: string;
  mode: DevicePreset;
  /* Implementation note. */
  refreshKey?: number;
  className?: string;
  style?: CSSProperties;
  onTitleUpdate?: (title: string) => void;
  onLoadingChange?: (loading: boolean) => void;
  onCanGoBackChange?: (can: boolean) => void;
  onCanGoForwardChange?: (can: boolean) => void;
  onError?: (msg: string) => void;
}

export interface WebviewHandle {
  reload: () => void;
  goBack: () => void;
  goForward: () => void;
  executeJS: (code: string) => Promise<unknown>;
}

// Implementation note.
// Implementation note.
type WebviewElement = HTMLElement & {
  src: string;
  getWebContentsId: () => number;
  reload: () => void;
  goBack: () => void;
  goForward: () => void;
  canGoBack: () => boolean;
  canGoForward: () => boolean;
  getTitle: () => string;
  isLoading: () => boolean;
  loadURL: (url: string) => Promise<void>;
};

export const WebviewRenderer = forwardRef<WebviewHandle, Props>(
  function WebviewRenderer(
    {
      url,
      mode,
      refreshKey = 0,
      className,
      style,
      onTitleUpdate,
      onLoadingChange,
      onCanGoBackChange,
      onCanGoForwardChange,
      onError,
    },
    imperativeRef,
  ) {
    const ref = useRef<WebviewElement | null>(null);
    const [ready, setReady] = useState(false);

    useImperativeHandle(
      imperativeRef,
      () => ({
        reload: () => ref.current?.reload(),
        goBack: () => ref.current?.goBack(),
        goForward: () => ref.current?.goForward(),
        executeJS: async (code: string) => {
          const wv = ref.current;
          const api = window.echo;
          if (!wv || !api) return undefined;
          return api.browser.executeJS(wv.getWebContentsId(), code);
        },
      }),
      [],
    );

    // Implementation note.
    // Implementation note.
    useEffect(() => {
      const wv = ref.current;
      const api = window.echo;
      if (!wv || !api || !ready) return;
      let cancelled = false;
      (async () => {
        try {
          const id = wv.getWebContentsId();
          await api.browser.setDevice(id, mode);
          if (cancelled) return;
          // Implementation note.
          wv.reload();
        } catch (err) {
          swallow(err);
          if (!cancelled) {
            onError?.(err instanceof Error ? err.message : String(err));
          }
        }
      })();
      return () => {
        cancelled = true;
      };
    }, [mode, ready, onError]);

    // Implementation note.
    useEffect(() => {
      const wv = ref.current;
      if (!wv) return;

      const onDomReady = () => {
        setReady(true);
        onCanGoBackChange?.(wv.canGoBack());
        onCanGoForwardChange?.(wv.canGoForward());
      };
      const onStartLoading = () => onLoadingChange?.(true);
      const onStopLoading = () => {
        onLoadingChange?.(false);
        onCanGoBackChange?.(wv.canGoBack());
        onCanGoForwardChange?.(wv.canGoForward());
      };
      const onPageTitleUpdated = (e: Event & { title?: string }) => {
        onTitleUpdate?.(e.title || wv.getTitle());
      };
      const onFailLoad = (
        e: Event & { errorCode?: number; errorDescription?: string },
      ) => {
        // Implementation note.
        if (e.errorCode === -3) return;
        onError?.(
          `${e.errorDescription ?? "load failed"} (${e.errorCode ?? "?"})`,
        );
      };

      wv.addEventListener("dom-ready", onDomReady);
      wv.addEventListener("did-start-loading", onStartLoading);
      wv.addEventListener("did-stop-loading", onStopLoading);
      wv.addEventListener(
        "page-title-updated",
        onPageTitleUpdated as EventListener,
      );
      wv.addEventListener("did-fail-load", onFailLoad as EventListener);

      return () => {
        wv.removeEventListener("dom-ready", onDomReady);
        wv.removeEventListener("did-start-loading", onStartLoading);
        wv.removeEventListener("did-stop-loading", onStopLoading);
        wv.removeEventListener(
          "page-title-updated",
          onPageTitleUpdated as EventListener,
        );
        wv.removeEventListener("did-fail-load", onFailLoad as EventListener);
      };
    }, [
      onCanGoBackChange,
      onCanGoForwardChange,
      onError,
      onLoadingChange,
      onTitleUpdate,
    ]);

    // Implementation note.
    // Implementation note.
    // Implementation note.
    useEffect(() => {
      const wv = ref.current;
      if (!wv) return;
      wv.setAttribute("allowpopups", "true");
    }, [refreshKey]);

    return (
      <webview
        key={refreshKey}
        ref={ref as unknown as React.RefObject<HTMLElement>}
        src={url}
        partition="persist:echo-browser"
        className={className}
        style={{
          display: "inline-flex",
          width: "100%",
          height: "100%",
          ...style,
        }}
      />
    );
  },
);
