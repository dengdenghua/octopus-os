import { CrosshairIcon, SendIcon, SparklesIcon, XIcon } from "lucide-react";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { dispatchInspectSelected } from "./inspect-bus";

type IncomingMessage =
  | { type: "echo:inspect:ready" }
  | { type: "echo:inspect:state"; active: boolean }
  | {
      type: "echo:inspect:select";
      payload: {
        selector: string;
        tagName: string;
        outerHTML: string;
        textContent: string;
        rect: { x: number; y: number; w: number; h: number };
      };
    };

type SelectedElement = Extract<
  IncomingMessage,
  { type: "echo:inspect:select" }
>["payload"];

export function InspectOverlay({
  iframeRef,
  filepath,
  bridgeToken,
  enabled,
  onRequestAiEdit,
  onPrepareInspect,
  busy = false,
  className,
  children,
}: {
  iframeRef: React.RefObject<HTMLIFrameElement | null>;
  filepath: string;
  bridgeToken?: string;
  /** When false, renders children pass-through with no inspect chrome. */
  enabled: boolean;
  onRequestAiEdit?: (
    selection: SelectedElement,
    instruction: string,
  ) => boolean | void;
  onPrepareInspect?: () => void;
  busy?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const { t } = useI18n();
  const [active, setActive] = useState(false);
  const [iframeReady, setIframeReady] = useState(false);
  const [selected, setSelected] = useState<SelectedElement | null>(null);
  const [instruction, setInstruction] = useState("");
  const [pendingActivation, setPendingActivation] = useState(false);
  const filepathRef = useRef(filepath);
  filepathRef.current = filepath;

  useEffect(() => {
    if (!enabled) return;
    function onMessage(e: MessageEvent) {
      if (!iframeRef.current) return;
      if (e.source !== iframeRef.current.contentWindow) return;
      const data = e.data as IncomingMessage | null;
      if (!data || typeof data !== "object") return;
      if (
        bridgeToken &&
        (data as IncomingMessage & { echoBridgeToken?: unknown })
          .echoBridgeToken !== bridgeToken
      ) {
        return;
      }
      if (data.type === "echo:inspect:ready") {
        setIframeReady(true);
      } else if (data.type === "echo:inspect:state") {
        setActive(!!data.active);
      } else if (data.type === "echo:inspect:select") {
        dispatchInspectSelected({
          ...data.payload,
          filepath: filepathRef.current,
        });
        setSelected(data.payload);
        setActive(false);
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [bridgeToken, enabled, iframeRef]);

  // Reset readiness when the file changes — new srcDoc means new injected script lifecycle.
  useEffect(() => {
    setIframeReady(false);
    setActive(false);
    setSelected(null);
    setInstruction("");
    setPendingActivation(false);
  }, [filepath]);

  useEffect(() => {
    if (!iframeReady || !pendingActivation) return;
    iframeRef.current?.contentWindow?.postMessage(
      { type: "echo:inspect:enable", echoBridgeToken: bridgeToken },
      "*",
    );
    setActive(true);
    setPendingActivation(false);
  }, [bridgeToken, iframeReady, iframeRef, pendingActivation]);

  const dismissSelection = useCallback(() => {
    setSelected(null);
    setInstruction("");
  }, []);

  const submitEdit = useCallback(() => {
    const nextInstruction = instruction.trim();
    if (!selected || !nextInstruction || busy) return;
    const accepted = onRequestAiEdit?.(selected, nextInstruction);
    if (accepted !== false) dismissSelection();
  }, [busy, dismissSelection, instruction, onRequestAiEdit, selected]);

  if (!enabled) {
    return (
      <div className={cn("relative size-full", className)}>{children}</div>
    );
  }

  function toggle() {
    // A URL-backed preview deliberately stays on its real origin until the
    // user asks to inspect. Always enter the prepared srcDoc mode first when
    // this callback is present; do not trust a preview page to self-report the
    // bridge as ready through a lookalike postMessage.
    if (onPrepareInspect) {
      setPendingActivation(true);
      setIframeReady(false);
      onPrepareInspect();
      return;
    }
    const win = iframeRef.current?.contentWindow;
    if (!win) return;
    const next = !active;
    win.postMessage(
      {
        type: next ? "echo:inspect:enable" : "echo:inspect:disable",
        echoBridgeToken: bridgeToken,
      },
      "*",
    );
    setActive(next);
  }

  return (
    <div className={cn("relative size-full", className)}>
      {children}
      <div className="pointer-events-none absolute top-2 right-2 z-10 flex items-center gap-1.5">
        {active && (
          <span className="pointer-events-none rounded-md bg-primary px-2 py-1 text-xs text-primary-foreground">
            {t.livePreview.inspectHint}
          </span>
        )}
        <Button
          aria-label={
            active ? t.livePreview.cancelInspect : t.livePreview.inspectElement
          }
          className={cn(
            "pointer-events-auto h-7 gap-1.5 px-2 text-xs",
            active && "bg-primary text-primary-foreground hover:bg-primary/90",
          )}
          disabled={!iframeReady && !onPrepareInspect}
          onClick={toggle}
          size="sm"
          title={
            iframeReady || onPrepareInspect
              ? active
                ? t.livePreview.cancelInspect
                : t.livePreview.inspectElement
              : t.livePreview.loading
          }
          type="button"
          variant={active ? "default" : "secondary"}
        >
          {active ? (
            <XIcon className="size-3" />
          ) : (
            <CrosshairIcon className="size-3" />
          )}
          {pendingActivation
            ? t.livePreview.loading
            : active
              ? t.livePreview.cancelInspect
              : t.livePreview.inspectElement}
        </Button>
      </div>
      {selected && (
        <div className="pointer-events-none absolute inset-x-3 bottom-3 z-20 flex justify-center">
          <div className="pointer-events-auto w-full max-w-xl rounded-xl border border-primary/25 bg-background/95 p-3 shadow-[var(--shadow-lg)] backdrop-blur">
            <div className="mb-2 flex items-start gap-2">
              <SparklesIcon className="mt-0.5 size-4 shrink-0 text-primary" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium">
                    {t.livePreview.aiEditTitle}
                  </span>
                  <code className="truncate rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                    {selected.selector || selected.tagName}
                  </code>
                </div>
                {selected.textContent && (
                  <p className="mt-1 truncate text-[11px] text-muted-foreground">
                    {selected.textContent}
                  </p>
                )}
              </div>
              <Button
                aria-label={t.livePreview.aiEditCancel}
                className="size-6 shrink-0"
                onClick={dismissSelection}
                size="icon-sm"
                type="button"
                variant="ghost"
              >
                <XIcon className="size-3.5" />
              </Button>
            </div>
            <div className="flex gap-2">
              <Input
                autoFocus
                disabled={busy}
                onChange={(event) => setInstruction(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                    event.preventDefault();
                    submitEdit();
                  }
                  if (event.key === "Escape") dismissSelection();
                }}
                placeholder={t.livePreview.aiEditPlaceholder}
                value={instruction}
              />
              <Button
                className="shrink-0 gap-1.5"
                disabled={!instruction.trim() || busy || !onRequestAiEdit}
                onClick={submitEdit}
                size="sm"
                type="button"
              >
                <SendIcon className="size-3.5" />
                {t.livePreview.aiEditSend}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
