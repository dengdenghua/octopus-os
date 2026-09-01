import { useControllableState } from "@radix-ui/react-use-controllable-state";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import { BrainIcon, ChevronDownIcon } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import {
  Suspense,
  createContext,
  lazy,
  memo,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";


const LazyStreamdown = lazy(() => import("./streamdown-host"));

type ReasoningContextValue = {
  isStreaming: boolean;
  isOpen: boolean;
  setIsOpen: (open: boolean) => void;
  duration: number | undefined;
};

const ReasoningContext = createContext<ReasoningContextValue | null>(null);

export const useReasoning = () => {
  const context = useContext(ReasoningContext);
  if (!context) {
    throw new Error("Reasoning components must be used within Reasoning");
  }
  return context;
};

export type ReasoningProps = ComponentProps<typeof Collapsible> & {
  isStreaming?: boolean;
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  duration?: number;
};

const MS_IN_S = 1000;

export const Reasoning = memo(
  ({
    className,
    isStreaming = false,
    open,
    defaultOpen = true,
    onOpenChange,
    duration: durationProp,
    children,
    ...props
  }: ReasoningProps) => {
    const [isOpen, setIsOpen] = useControllableState({
      prop: open,
      defaultProp: defaultOpen,
      onChange: onOpenChange,
    });
    const [duration, setDuration] = useControllableState({
      prop: durationProp,
      defaultProp: undefined,
    });

    const [startTime, setStartTime] = useState<number | null>(null);

    useEffect(() => {
      if (isStreaming) {
        if (startTime === null) {
          setStartTime(Date.now());
        }
      } else if (startTime !== null) {
        setDuration(Math.ceil((Date.now() - startTime) / MS_IN_S));
        setStartTime(null);
      }
    }, [isStreaming, setDuration, startTime]);

    // Auto-open only on the false→true edge of `isStreaming`. Tracking
    // `isOpen` here meant a user who manually collapsed the panel mid-stream
    // got it forced back open on the next render — the user's explicit
    // choice must win over the convenience default.
    const prevStreamingRef = useRef(isStreaming);
    useEffect(() => {
      const wasStreaming = prevStreamingRef.current;
      prevStreamingRef.current = isStreaming;
      if (isStreaming && !wasStreaming) {
        setIsOpen(true);
      }
    }, [isStreaming, setIsOpen]);

    return (
      <ReasoningContext.Provider
        value={{ isStreaming, isOpen, setIsOpen, duration }}
      >
        <Collapsible
          className={cn("not-prose mb-4", className)}
          onOpenChange={setIsOpen}
          open={isOpen}
          {...props}
        >
          {children}
        </Collapsible>
      </ReasoningContext.Provider>
    );
  },
);

export type ReasoningTriggerProps = ComponentProps<
  typeof CollapsibleTrigger
> & {
  getThinkingMessage?: (isStreaming: boolean, duration?: number) => ReactNode;
};

export const ReasoningTrigger = memo(
  ({
    className,
    children,
    getThinkingMessage,
    ...props
  }: ReasoningTriggerProps) => {
    const { isStreaming, isOpen, duration } = useReasoning();
    const { t } = useI18n();
    const resolvedThinkingMessage =
      getThinkingMessage ??
      ((streaming: boolean, elapsed?: number) => {
        if (streaming || elapsed === 0) {
          return <span className="animate-pulse">{t.streaming.thinking}</span>;
        }
        if (elapsed === undefined) {
          return <span>{t.streaming.thoughtProcess}</span>;
        }
        return <span>{`${t.streaming.thoughtProcess} ${elapsed}s`}</span>;
      });

    return (
      <CollapsibleTrigger
        className={cn(
          "text-muted-foreground hover:text-foreground inline-flex w-full items-center gap-2 px-1 py-0.5 text-sm transition-colors hover:bg-muted/30",
          className,
        )}
        {...props}
      >
        {children ?? (
          <>
            <BrainIcon className="size-4" />
            {resolvedThinkingMessage(isStreaming, duration)}
            <ChevronDownIcon
              className={cn(
                "size-4 transition-transform",
                isOpen ? "rotate-180" : "rotate-0",
              )}
            />
          </>
        )}
      </CollapsibleTrigger>
    );
  },
);

export type ReasoningContentProps = ComponentProps<
  typeof CollapsibleContent
> & {
  children: string;
};

export const ReasoningContent = memo(
  ({ className, children, ...props }: ReasoningContentProps) => {
    const { isStreaming } = useReasoning();
    return (
      <CollapsibleContent
        className={cn(
          "mt-2 text-sm relative overflow-hidden",
          "data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-top-2 data-[state=open]:slide-in-from-top-2 text-muted-foreground data-[state=closed]:animate-out data-[state=open]:animate-in outline-none",
          "rounded-lg border border-border-subtle bg-muted/20 p-3 pl-4",
          isStreaming &&
            "border-l-primary/50 shadow-[inset_2px_0_0_0_var(--primary)]",
          className,
        )}
        {...props}
      >
        <Suspense
          fallback={
            <div className="whitespace-pre-wrap break-words">{children}</div>
          }
        >
          <LazyStreamdown {...props}>{children}</LazyStreamdown>
        </Suspense>
      </CollapsibleContent>
    );
  },
);

Reasoning.displayName = "Reasoning";
ReasoningTrigger.displayName = "ReasoningTrigger";
ReasoningContent.displayName = "ReasoningContent";
