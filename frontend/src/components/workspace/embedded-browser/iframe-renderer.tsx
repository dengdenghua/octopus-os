import { useCallback, useEffect, useRef, useState } from "react";
import { ExternalLinkIcon, GlobeIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { LoadingState } from "@/components/ui/state";
import { useI18n } from "@/core/i18n/hooks";

interface IframeRendererProps {
  url: string;
  width: number | "100%";
  onFallbackNeeded: () => void;
}

export function IframeRenderer({
  url,
  width,
  onFallbackNeeded,
}: IframeRendererProps) {
  const { t } = useI18n();
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [loading, setLoading] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const loadingRef = useRef(false);
  const timeoutRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const isDesktop = typeof window !== "undefined" && window.__ECHO_DESKTOP__;

  const handleLoad = useCallback(() => {
    loadingRef.current = false;
    setLoading(false);
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
  }, []);

  // React to URL changes via useEffect instead of render-phase side effects
  useEffect(() => {
    if (!url) return;

    loadingRef.current = true;
    setLoading(true);
    setBlocked(false);

    // Timeout-based detection: if iframe doesn't load in 5s, assume blocked
    timeoutRef.current = setTimeout(() => {
      if (loadingRef.current) {
        if (isDesktop) {
          onFallbackNeeded();
        } else {
          setBlocked(true);
          setLoading(false);
          loadingRef.current = false;
        }
      }
    }, 5000);

    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, [url, isDesktop, onFallbackNeeded]);

  if (!url) {
    return (
      <Empty className="h-full rounded-none border-0 bg-transparent">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <GlobeIcon />
          </EmptyMedia>
          <EmptyTitle>{t.browser.startBrowsingHint}</EmptyTitle>
        </EmptyHeader>
      </Empty>
    );
  }

  if (blocked) {
    return (
      <Empty className="h-full rounded-none border-0 bg-transparent">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <GlobeIcon />
          </EmptyMedia>
          <EmptyTitle>{t.browser.embeddedBlocked}</EmptyTitle>
          <EmptyDescription>
            {t.browser.embeddedBlockedDescription}
          </EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button asChild variant="outline" size="sm">
            <a href={url} target="_blank" rel="noopener noreferrer">
              <ExternalLinkIcon className="size-3.5" />
              {t.browser.openExternal}
            </a>
          </Button>
        </EmptyContent>
      </Empty>
    );
  }

  return (
    <div className="relative h-full w-full">
      {loading && (
        <div className="absolute inset-0 z-10 bg-background/55 backdrop-blur-sm">
          <LoadingState
            title={t.browser.loadingPage}
            className="h-full min-h-0 border-0 bg-transparent"
          />
        </div>
      )}
      <iframe
        ref={iframeRef}
        src={url}
        sandbox="allow-scripts allow-forms allow-popups"
        style={{
          width: typeof width === "number" ? `${width}px` : width,
          height: "100%",
        }}
        className="mx-auto border-0"
        onLoad={handleLoad}
        title="Embedded Browser"
      />
    </div>
  );
}
