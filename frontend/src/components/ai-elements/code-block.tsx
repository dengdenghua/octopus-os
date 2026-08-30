import DOMPurify from "dompurify";
import { Button } from "@/components/ui/button";
import { swallow } from "@/core/utils/log";
import { copyTextToClipboard } from "@/core/clipboard";
import { cn } from "@/lib/utils";
import { useI18n } from "@/core/i18n/hooks";
import { ArrowLeftRight, CheckIcon, CopyIcon, WrapText } from "lucide-react";
import { useTheme } from "next-themes";
import { useLocalStorage } from "@/hooks/use-local-storage";
import {
  type ComponentProps,
  createContext,
  type HTMLAttributes,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import type { BundledLanguage, ShikiTransformer } from "shiki";

type CodeBlockProps = HTMLAttributes<HTMLDivElement> & {
  code: string;
  language: BundledLanguage;
  showLineNumbers?: boolean;
  isStreaming?: boolean;
};

type CodeBlockContextType = {
  code: string;
  isStreaming: boolean;
};

const CodeBlockContext = createContext<CodeBlockContextType>({
  code: "",
  isStreaming: false,
});

const lineNumberTransformer: ShikiTransformer = {
  name: "line-numbers",
  line(node, line) {
    node.children.unshift({
      type: "element",
      tagName: "span",
      properties: {
        className: [
          "inline-block",
          "min-w-10",
          "mr-4",
          "text-right",
          "select-none",
          "text-muted-foreground",
        ],
      },
      children: [{ type: "text", value: String(line) }],
    });
  },
};

export async function highlightCode(
  code: string,
  language: BundledLanguage,
  showLineNumbers = false,
  theme: "one-light" | "one-dark-pro" = "one-dark-pro",
) {
  const { codeToHtml } = await import("shiki");
  const transformers: ShikiTransformer[] = showLineNumbers
    ? [lineNumberTransformer]
    : [];

  return await codeToHtml(code, {
    lang: language,
    theme,
    transformers,
  });
}

/**
 * Classes applied to the <pre> element in BOTH modes (plain stream and
 * Shiki-highlighted). Kept identical so that when we swap from the
 * streaming plain <pre> to Shiki's highlighted <pre> there is zero
 * geometry change — no height jump, no padding shift, no margin
 * re-collapse. The visual difference is only colors.
 */
const PRE_CLASS_OVERRIDES =
  "[&>pre]:m-0 [&>pre]:p-4 [&>pre]:text-sm [&>pre]:leading-6 " +
  "[&>pre]:bg-transparent! [&_code]:font-mono [&_code]:text-sm";

const WRAP_PRE_CLASS = "[&>pre]:whitespace-pre-wrap";
const SCROLL_PRE_CLASS = "[&>pre]:whitespace-pre";

function LightweightCodeBlock({ code, wrap }: { code: string; wrap: boolean }) {
  // Exactly the same <pre> shape the Shiki output uses — just without
  // syntax coloring. The `overflow-auto` wrapper and class contract
  // matches the highlighted variant below.
  return (
    <pre
      className={cn(
        "m-0 p-4 text-sm leading-6 bg-transparent font-mono",
        wrap ? "whitespace-pre-wrap" : "whitespace-pre",
      )}
    >
      <code>{code}</code>
      <span className="inline-block w-0.5 h-4 bg-primary/60 ml-0.5 align-middle animate-pulse" />
    </pre>
  );
}

/**
 * Placeholder for a SETTLED (historical) code block whose first shiki
 * highlight has not arrived yet. Readers reloading a conversation should
 * never watch raw code "morph" into highlighted code — a neutral skeleton
 * holds the geometry (same p-4 padding, one row per source line at
 * leading-6 rhythm) until the real render is ready. Static bars only, no
 * shimmer/pulse: this is a wait state, not live activity.
 */
function SettledCodeSkeleton({ code }: { code: string }) {
  const lines = Math.max(code.split("\n").length, 1);
  return (
    <div className="flex flex-col p-4" aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="flex h-6 items-center">
          <div
            className={cn(
              "h-3 rounded-sm bg-muted-foreground/10",
              i % 3 === 2 ? "w-2/3" : "w-full",
            )}
          />
        </div>
      ))}
    </div>
  );
}

export const CodeBlock = ({
  code,
  language,
  showLineNumbers = false,
  isStreaming = false,
  className,
  children,
  ...props
}: CodeBlockProps) => {
  const { resolvedTheme } = useTheme();
  const { t } = useI18n();
  const isDark = resolvedTheme === "dark";
  const shikiTheme = isDark ? "one-dark-pro" : "one-light";
  const [html, setHtml] = useState<string>("");
  const [wrap, setWrap] = useLocalStorage("echo:code-block-wrap", true);
  const mountedRef = useRef(false);
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const highlightRequestRef = useRef(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      highlightRequestRef.current++;
      if (highlightTimerRef.current) {
        clearTimeout(highlightTimerRef.current);
        highlightTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (highlightTimerRef.current) {
      clearTimeout(highlightTimerRef.current);
      highlightTimerRef.current = null;
    }

    const requestId = ++highlightRequestRef.current;
    const applyHighlight = (result: string) => {
      if (mountedRef.current && highlightRequestRef.current === requestId) {
        setHtml(result);
      }
    };

    // A rejected highlight (grammar/engine failure) must not surface as an
    // unhandled rejection — the block simply keeps its plain-text render.
    const highlightSafely = (promise: Promise<string>) => {
      promise.then(applyHighlight).catch((error: unknown) => {
        swallow(error, "shiki-highlight");
      });
    };

    if (isStreaming) {
      // Keep the last rendered highlight instead of clearing it on every
      // token. Clearing per-token made the block flash back to plain text
      // between debounced re-highlights. The stale highlight is replaced by
      // the debounced one as soon as the stream pauses.
      highlightTimerRef.current = setTimeout(() => {
        highlightTimerRef.current = null;
        highlightSafely(
          highlightCode(code, language, showLineNumbers, shikiTheme),
        );
      }, 150);
    } else {
      // Settled code: highlight immediately and KEEP the stale streaming
      // highlight on screen until the fresh one arrives. Clearing here used
      // to flash one frame of unhighlighted plain text between the stream
      // and the final highlight.
      highlightSafely(
        highlightCode(code, language, showLineNumbers, shikiTheme),
      );
    }

    return () => {
      if (highlightTimerRef.current) {
        clearTimeout(highlightTimerRef.current);
        highlightTimerRef.current = null;
      }
    };
  }, [code, language, showLineNumbers, isStreaming, shikiTheme]);

  const showHighlighted = !!html;
  const streamingHeader = isStreaming && !showHighlighted;

  return (
    <CodeBlockContext.Provider value={{ code, isStreaming }}>
      <div
        className={cn(
          "group bg-muted/30 text-foreground relative size-full overflow-hidden rounded-lg border",
          className,
        )}
        {...props}
      >
        <div className="flex h-8 items-center justify-between gap-2 border-b bg-muted/40 px-3">
          <div className="flex items-center gap-2">
            <span className="text-micro text-muted-foreground font-mono uppercase">
              {language}
            </span>
            {streamingHeader && (
              <>
                <span className="text-micro text-muted-foreground">·</span>
                <span className="text-micro text-muted-foreground animate-pulse">
                  {t.streaming.generating}
                </span>
              </>
            )}
          </div>
          <div className="flex items-center gap-2">
            {!isStreaming && (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="shrink-0 h-6 w-6"
                title={
                  wrap ? t.streaming.codeBlockScroll : t.streaming.codeBlockWrap
                }
                aria-label={
                  wrap ? t.streaming.codeBlockScroll : t.streaming.codeBlockWrap
                }
                onClick={() => setWrap((value) => !value)}
              >
                {wrap ? <ArrowLeftRight size={12} /> : <WrapText size={12} />}
              </Button>
            )}
            {children}
          </div>
        </div>

        <div className="relative size-full">
          {showHighlighted ? (
            <div
              className={cn(
                "size-full overflow-auto",
                PRE_CLASS_OVERRIDES,
                wrap ? WRAP_PRE_CLASS : SCROLL_PRE_CLASS,
              )}
              // biome-ignore lint/security/noDangerouslySetInnerHtml: sanitized via DOMPurify (audit C3/M4).
              dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(html) }}
            />
          ) : isStreaming ? (
            <div className="size-full overflow-auto">
              <LightweightCodeBlock code={code} wrap={wrap} />
            </div>
          ) : (
            // Settled historical block still waiting for its first
            // highlight: show a geometry-stable placeholder instead of
            // flashing raw text that later "reorganizes" into colors.
            <SettledCodeSkeleton code={code} />
          )}
        </div>
      </div>
    </CodeBlockContext.Provider>
  );
};

export type CodeBlockCopyButtonProps = ComponentProps<typeof Button> & {
  onCopy?: () => void;
  onError?: (error: Error) => void;
  timeout?: number;
  showWhileStreaming?: boolean;
};

export const CodeBlockCopyButton = ({
  onCopy,
  onError,
  timeout = 2000,
  showWhileStreaming = false,
  children,
  className,
  ...props
}: CodeBlockCopyButtonProps) => {
  const [isCopied, setIsCopied] = useState(false);
  const { code, isStreaming } = useContext(CodeBlockContext);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const copyToClipboard = async () => {
    if (typeof window === "undefined" || !navigator?.clipboard?.writeText) {
      onError?.(new Error("Clipboard API not available"));
      return;
    }

    try {
      await copyTextToClipboard(code);
      setIsCopied(true);
      onCopy?.();
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setIsCopied(false), timeout);
    } catch (error) {
      swallow(error);
      onError?.(error as Error);
    }
  };

  const Icon = isCopied ? CheckIcon : CopyIcon;

  if (isStreaming && !showWhileStreaming) {
    return null;
  }

  return (
    <Button
      className={cn("shrink-0 h-6 w-6", className)}
      onClick={copyToClipboard}
      size="icon"
      variant="ghost"
      {...props}
    >
      {children ?? <Icon size={12} />}
    </Button>
  );
};
