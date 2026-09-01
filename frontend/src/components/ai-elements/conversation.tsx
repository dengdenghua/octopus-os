import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { ArrowDownIcon } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { StickToBottom, useStickToBottomContext } from "use-stick-to-bottom";

export type ConversationProps = ComponentProps<typeof StickToBottom>;

export const Conversation = ({ className, ...props }: ConversationProps) => (
  <StickToBottom
    className={cn(
      "relative flex-1 overflow-x-hidden overflow-y-hidden",
      className,
    )}
    // Streaming content changes height every frame. A smooth resize scroll
    // starts a new animation for each measurement, which can visibly wobble
    // while the user is wheel-scrolling or when the latest button is pressed.
    // Keep resize reconciliation deterministic; explicit user navigation can
    // still opt into animation below.
    initial="instant"
    resize="instant"
    role="log"
    {...props}
  />
);

export type ConversationContentProps = ComponentProps<
  typeof StickToBottom.Content
>;

export const ConversationContent = ({
  className,
  ...props
}: ConversationContentProps) => (
  <StickToBottom.Content
    className={cn("flex flex-col gap-8 overflow-x-hidden p-4", className)}
    {...props}
  />
);

export type ConversationEmptyStateProps = ComponentProps<"div"> & {
  title?: string;
  description?: string;
  icon?: React.ReactNode;
};

export const ConversationEmptyState = ({
  className,
  title = "No messages yet",
  description = "Start a conversation to see messages here",
  icon,
  children,
  ...props
}: ConversationEmptyStateProps) => (
  <div
    className={cn(
      "flex size-full flex-col items-center justify-center gap-3 p-8 text-center",
      className,
    )}
    {...props}
  >
    {children ?? (
      <>
        {icon && (
          <div className="flex items-center justify-center rounded-lg bg-primary/5 p-5">
            <div className="text-muted-foreground [&_svg]:size-8">{icon}</div>
          </div>
        )}
        <div className="space-y-1.5 max-w-xs">
          <h3 className="text-sm font-medium">{title}</h3>
          {description && (
            <p className="text-muted-foreground text-sm leading-relaxed">
              {description}
            </p>
          )}
        </div>
      </>
    )}
  </div>
);

export type ConversationScrollButtonProps = Omit<
  ComponentProps<typeof Button>,
  "children"
> & {
  children?: ReactNode;
  activityKey?: string | number;
  activityLabel?: (count: number) => ReactNode;
};

export const ConversationScrollButton = ({
  activityKey,
  activityLabel,
  className,
  children,
  ...props
}: ConversationScrollButtonProps) => {
  const { escapedFromLock, isAtBottom, scrollToBottom } =
    useStickToBottomContext();
  const previousActivityKey = useRef(activityKey);
  const [pendingActivityCount, setPendingActivityCount] = useState(0);

  useEffect(() => {
    if (Object.is(previousActivityKey.current, activityKey)) return;
    previousActivityKey.current = activityKey;
    // The underlying ResizeObserver can briefly lose its bottom lock when a
    // streamed response grows from shorter than the viewport to taller than
    // it. That left a brand-new task parked at scrollTop=0 even though the
    // reader never moved away. Preserve the library's explicit escape signal:
    // only force-follow when no wheel/selection/up-scroll released the lock.
    if (!escapedFromLock) {
      void scrollToBottom({ animation: "instant", ignoreEscapes: true });
      return;
    }
    // activityKey can advance once per streamed frame. Treat unseen activity
    // as a boolean signal instead of counting transport chunks; otherwise a
    // long answer produces labels such as "685 new updates" and keeps
    // re-rendering this control while the reader is browsing older content.
    if (!isAtBottom) setPendingActivityCount(1);
  }, [activityKey, escapedFromLock, isAtBottom, scrollToBottom]);

  useEffect(() => {
    if (isAtBottom) setPendingActivityCount(0);
  }, [isAtBottom]);

  const handleScrollToBottom = useCallback(() => {
    setPendingActivityCount(0);
    // Smooth for the explicit button so the jump doesn't feel abrupt; the
    // streaming auto-follow above stays "instant" to avoid jitter.
    scrollToBottom({ animation: "smooth" });
  }, [scrollToBottom]);

  const label =
    pendingActivityCount > 0 && activityLabel
      ? activityLabel(pendingActivityCount)
      : children;

  return (
    !isAtBottom && (
      <Button
        className={cn(
          "absolute bottom-4 left-1/2 z-40 -translate-x-1/2 rounded-full border-border-default bg-background/92 px-3 text-xs shadow-[var(--shadow-md)] shadow-black/10 backdrop-blur-md hover:bg-background",
          className,
        )}
        onClick={handleScrollToBottom}
        size={label ? "sm" : "icon-sm"}
        type="button"
        variant="outline"
        {...props}
      >
        <ArrowDownIcon className="size-4" />
        {label}
      </Button>
    )
  );
};
