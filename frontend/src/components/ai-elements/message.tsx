import { Button } from "@/components/ui/button";
import { ButtonGroup, ButtonGroupText } from "@/components/ui/button-group";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import type { FileUIPart, UIMessage } from "ai";
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  PaperclipIcon,
  XIcon,
} from "lucide-react";
import type { ComponentProps, HTMLAttributes, ReactElement } from "react";
import {
  Suspense,
  createContext,
  lazy,
  memo,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import type { StreamdownProps } from "./streamdown-host";

const LazyStreamdown = lazy(async () => {
  const mod = await import("./streamdown-host");
  return { default: mod.LocalizedStreamdown };
});

export type MessageProps = HTMLAttributes<HTMLDivElement> & {
  from: UIMessage["role"];
};

export const Message = ({ className, from, ...props }: MessageProps) => (
  <div
    className={cn(
      "group flex w-full flex-col gap-2",
      from === "user" ? "is-user ml-auto justify-end" : "is-assistant",
      className,
    )}
    {...props}
  />
);

export type MessageContentProps = HTMLAttributes<HTMLDivElement>;

export const MessageContent = ({
  children,
  className,
  ...props
}: MessageContentProps) => (
  <div
    className={cn(
      "flex max-w-full flex-col gap-2 overflow-visible select-text",
      // Chat-bubble style for the user side (messenger convention): a
      // filled, right-aligned bubble so your own messages read as distinct
      // from the assistant's flat streamed output. The assistant stays
      // bubble-less because it streams long markdown, code and traces that
      // a tinted container would fight with.
      //
      // Width behavior split by role to avoid the CJK vertical-stacking
      // bug: `w-fit min-w-0` together with `word-break:break-word` defaults
      // Implementation note.
      // min-content width = one character, producing one-char-per-line.
      // For user bubbles we use `min-w-fit` (at least content width) +
      // `max-w-[85%]` (cap) so the bubble is as wide as its content but
      // never collapses below it. Assistant bubbles keep `w-full min-w-0`
      // because they need to span and wrap long streamed markdown.
      "group-[.is-user]:ml-auto group-[.is-user]:min-w-fit group-[.is-user]:max-w-[85%] group-[.is-user]:w-auto",
      // A solid `--primary` fill read as a loud colored slab next to the
      // neutral surfaces the rest of the timeline uses (tool groups,
      // clarification cards and composer chrome are all `bg-muted/25..40`
      // + `border-border`). Use that same neutral language: a plain muted
      // grey bubble, which marks the speaker by shape and alignment rather
      // than by colour. Text stays `--foreground`.
      "group-[.is-user]:bg-muted group-[.is-user]:text-foreground",
      "group-[.is-user]:border group-[.is-user]:border-border",
      "group-[.is-user]:rounded-2xl group-[.is-user]:rounded-br-md",
      "group-[.is-user]:px-3.5 group-[.is-user]:py-2",
      "group-[.is-assistant]:w-full group-[.is-assistant]:min-w-0",
      "group-[.is-assistant]:text-foreground",
      className,
    )}
    {...props}
  >
    {children}
  </div>
);

export type MessageActionsProps = ComponentProps<"div">;

export const MessageActions = ({
  className,
  children,
  ...props
}: MessageActionsProps) => (
  <div className={cn("flex items-center gap-1", className)} {...props}>
    {children}
  </div>
);

export type MessageActionProps = ComponentProps<typeof Button> & {
  tooltip?: string;
  label?: string;
};

export const MessageAction = ({
  tooltip,
  children,
  label,
  variant = "ghost",
  size = "icon-sm",
  ...props
}: MessageActionProps) => {
  const button = (
    <Button size={size} type="button" variant={variant} {...props}>
      {children}
      <span className="sr-only">{label || tooltip}</span>
    </Button>
  );

  if (tooltip) {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>{button}</TooltipTrigger>
          <TooltipContent>
            <p>{tooltip}</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  return button;
};

type MessageBranchContextType = {
  currentBranch: number;
  totalBranches: number;
  goToPrevious: () => void;
  goToNext: () => void;
  branches: ReactElement[];
  setBranches: (branches: ReactElement[]) => void;
};

const MessageBranchContext = createContext<MessageBranchContextType | null>(
  null,
);

const useMessageBranch = () => {
  const context = useContext(MessageBranchContext);

  if (!context) {
    throw new Error(
      "MessageBranch components must be used within MessageBranch",
    );
  }

  return context;
};

export type MessageBranchProps = HTMLAttributes<HTMLDivElement> & {
  defaultBranch?: number;
  onBranchChange?: (branchIndex: number) => void;
};

export const MessageBranch = ({
  defaultBranch = 0,
  onBranchChange,
  className,
  ...props
}: MessageBranchProps) => {
  const [currentBranch, setCurrentBranch] = useState(defaultBranch);
  const [branches, setBranches] = useState<ReactElement[]>([]);

  const handleBranchChange = (newBranch: number) => {
    setCurrentBranch(newBranch);
    onBranchChange?.(newBranch);
  };

  const goToPrevious = () => {
    const newBranch =
      currentBranch > 0 ? currentBranch - 1 : branches.length - 1;
    handleBranchChange(newBranch);
  };

  const goToNext = () => {
    const newBranch =
      currentBranch < branches.length - 1 ? currentBranch + 1 : 0;
    handleBranchChange(newBranch);
  };

  const contextValue: MessageBranchContextType = {
    currentBranch,
    totalBranches: branches.length,
    goToPrevious,
    goToNext,
    branches,
    setBranches,
  };

  return (
    <MessageBranchContext.Provider value={contextValue}>
      <div
        className={cn("grid w-full gap-2 [&>div]:pb-0", className)}
        {...props}
      />
    </MessageBranchContext.Provider>
  );
};

export type MessageBranchContentProps = HTMLAttributes<HTMLDivElement>;

export const MessageBranchContent = ({
  children,
  ...props
}: MessageBranchContentProps) => {
  const { currentBranch, setBranches, branches } = useMessageBranch();
  const childrenArray = useMemo(
    () => (Array.isArray(children) ? children : [children]),
    [children],
  );

  // Use useEffect to update branches when they change
  useEffect(() => {
    if (branches.length !== childrenArray.length) {
      setBranches(childrenArray);
    }
  }, [childrenArray, branches, setBranches]);

  return childrenArray.map((branch, index) => (
    <div
      className={cn(
        "grid gap-2 overflow-hidden [&>div]:pb-0",
        index === currentBranch ? "block" : "hidden",
      )}
      key={branch.key}
      {...props}
    >
      {branch}
    </div>
  ));
};

export type MessageBranchSelectorProps = HTMLAttributes<HTMLDivElement> & {
  from: UIMessage["role"];
};

export const MessageBranchSelector = ({
  className,
  from,
  ...props
}: MessageBranchSelectorProps) => {
  const { totalBranches } = useMessageBranch();

  // Don't render if there's only one branch
  if (totalBranches <= 1) {
    return null;
  }

  return (
    <ButtonGroup
      className={cn(
        "[&>*:not(:first-child)]:rounded-l-md [&>*:not(:last-child)]:rounded-r-md",
        className,
      )}
      data-from={from}
      orientation="horizontal"
      {...props}
    />
  );
};

export type MessageBranchPreviousProps = ComponentProps<typeof Button>;

export const MessageBranchPrevious = ({
  children,
  ...props
}: MessageBranchPreviousProps) => {
  const { goToPrevious, totalBranches } = useMessageBranch();
  const { t } = useI18n();

  return (
    <Button
      aria-label={t.message.previousBranch}
      disabled={totalBranches <= 1}
      onClick={goToPrevious}
      size="icon-sm"
      type="button"
      variant="ghost"
      {...props}
    >
      {children ?? <ChevronLeftIcon size={14} />}
    </Button>
  );
};

export type MessageBranchNextProps = ComponentProps<typeof Button>;

export const MessageBranchNext = ({
  children,
  className,
  ...props
}: MessageBranchNextProps) => {
  const { goToNext, totalBranches } = useMessageBranch();
  const { t } = useI18n();

  return (
    <Button
      aria-label={t.message.nextBranch}
      className={className}
      disabled={totalBranches <= 1}
      onClick={goToNext}
      size="icon-sm"
      type="button"
      variant="ghost"
      {...props}
    >
      {children ?? <ChevronRightIcon size={14} />}
    </Button>
  );
};

export type MessageBranchPageProps = HTMLAttributes<HTMLSpanElement>;

export const MessageBranchPage = ({
  className,
  ...props
}: MessageBranchPageProps) => {
  const { currentBranch, totalBranches } = useMessageBranch();
  const { t } = useI18n();

  return (
    <ButtonGroupText
      className={cn(
        "text-muted-foreground border-none bg-transparent shadow-none",
        className,
      )}
      {...props}
    >
      {t.message.branchPosition(currentBranch + 1, totalBranches)}
    </ButtonGroupText>
  );
};

export type MessageResponseProps = StreamdownProps;

export const MessageResponse = memo(
  ({ className, children, ...props }: MessageResponseProps) => (
    <Suspense
      fallback={
        <div
          className={cn(
            "size-full select-text",
            typeof children === "string" &&
              "whitespace-pre-wrap break-words text-foreground",
            className,
          )}
          aria-busy="true"
        >
          {typeof children === "string" ? (
            children
          ) : (
            <div className="space-y-2">
              <div className="h-4 w-3/5 rounded-sm bg-muted-foreground/10" />
              <div className="h-4 w-full rounded-sm bg-muted-foreground/10" />
              <div className="h-4 w-4/5 rounded-sm bg-muted-foreground/10" />
              <div className="h-4 w-2/3 rounded-sm bg-muted-foreground/10" />
            </div>
          )}
        </div>
      }
    >
      <LazyStreamdown
        className={cn(
          "size-full select-text [&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
          className,
        )}
        {...props}
      >
        {children}
      </LazyStreamdown>
    </Suspense>
  ),
);

MessageResponse.displayName = "MessageResponse";

export type MessageAttachmentProps = HTMLAttributes<HTMLDivElement> & {
  data: FileUIPart;
  className?: string;
  onRemove?: () => void;
};

export function MessageAttachment({
  data,
  className,
  onRemove,
  ...props
}: MessageAttachmentProps) {
  const { t } = useI18n();
  const filename = data.filename || "";
  const mediaType =
    data.mediaType?.startsWith("image/") && data.url ? "image" : "file";
  const isImage = mediaType === "image";
  const attachmentLabel =
    filename ||
    (isImage ? t.message.imageAttachment : t.message.attachmentFallback);

  return (
    <div
      className={cn(
        "group relative size-24 overflow-hidden rounded-lg",
        className,
      )}
      {...props}
    >
      {isImage ? (
        <>
          <img
            alt={filename || t.message.imageAttachment}
            className="size-full object-cover"
            height={100}
            src={data.url}
            width={100}
          />
          {onRemove && (
            <Button
              aria-label={t.message.removeAttachment}
              className="bg-background/80 hover:bg-background absolute top-2 right-2 size-6 p-0 opacity-0 backdrop-blur-sm transition-opacity group-hover:opacity-100 [&>svg]:size-3"
              onClick={(e) => {
                e.stopPropagation();
                onRemove();
              }}
              type="button"
              variant="ghost"
            >
              <XIcon />
              <span className="sr-only">{t.message.removeAttachment}</span>
            </Button>
          )}
        </>
      ) : (
        <>
          <Tooltip>
            <TooltipTrigger asChild>
              <div className="bg-muted text-muted-foreground flex size-full shrink-0 items-center justify-center rounded-lg">
                <PaperclipIcon className="size-4" />
              </div>
            </TooltipTrigger>
            <TooltipContent>
              <p>{attachmentLabel}</p>
            </TooltipContent>
          </Tooltip>
          {onRemove && (
            <Button
              aria-label={t.message.removeAttachment}
              className="hover:bg-accent size-6 shrink-0 p-0 opacity-0 transition-opacity group-hover:opacity-100 [&>svg]:size-3"
              onClick={(e) => {
                e.stopPropagation();
                onRemove();
              }}
              type="button"
              variant="ghost"
            >
              <XIcon />
              <span className="sr-only">{t.message.removeAttachment}</span>
            </Button>
          )}
        </>
      )}
    </div>
  );
}

export type MessageAttachmentsProps = ComponentProps<"div">;

export function MessageAttachments({
  children,
  className,
  ...props
}: MessageAttachmentsProps) {
  if (!children) {
    return null;
  }

  return (
    <div
      className={cn(
        "ml-auto flex w-fit flex-wrap items-start gap-2",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

export type MessageToolbarProps = ComponentProps<"div">;

export const MessageToolbar = ({
  className,
  children,
  ...props
}: MessageToolbarProps) => (
  <div
    className={cn(
      "mt-4 flex w-full items-center justify-between gap-4",
      className,
    )}
    {...props}
  >
    {children}
  </div>
);
