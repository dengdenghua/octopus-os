import {
  AlertCircleIcon,
  CheckCircle2Icon,
  ClockIcon,
  Loader2Icon,
  PauseCircleIcon,
  RefreshCwIcon,
  XCircleIcon,
} from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export type StatusTone =
  | "idle"
  | "queued"
  | "running"
  | "success"
  | "warning"
  | "error"
  | "paused";

const STATUS_STYLES: Record<StatusTone, string> = {
  idle: "border-border-default bg-muted/35 text-muted-foreground",
  queued:
    "border-warning/25 bg-warning/10 text-warning",
  running:
    "border-primary/25 bg-primary/10 text-primary [&_[data-status-dot]]:animate-pulse",
  success:
    "border-success/25 bg-success/10 text-success",
  warning:
    "border-warning/25 bg-warning/10 text-warning",
  error: "border-destructive/25 bg-destructive/10 text-destructive",
  paused: "border-info/25 bg-info/10 text-info dark:text-info",
};

const STATUS_ICONS: Record<StatusTone, typeof ClockIcon> = {
  idle: ClockIcon,
  queued: ClockIcon,
  running: Loader2Icon,
  success: CheckCircle2Icon,
  warning: AlertCircleIcon,
  error: XCircleIcon,
  paused: PauseCircleIcon,
};

export function StatusBadge({
  children,
  className,
  dot = true,
  icon,
  tone = "idle",
}: {
  children: ReactNode;
  className?: string;
  dot?: boolean;
  icon?: boolean;
  tone?: StatusTone;
}) {
  const Icon = STATUS_ICONS[tone];
  return (
    <span
      data-slot="status-badge"
      data-tone={tone}
      className={cn(
        "inline-flex h-6 min-w-0 items-center gap-1.5 rounded-lg border px-2 text-mini font-medium leading-none",
        STATUS_STYLES[tone],
        className,
      )}
    >
      {icon ? (
        <Icon
          className={cn(
            "size-3 shrink-0",
            tone === "running" && "animate-spin",
          )}
        />
      ) : dot ? (
        <span
          data-status-dot
          className="size-1.5 shrink-0 rounded-full bg-current"
        />
      ) : null}
      <span className="truncate">{children}</span>
    </span>
  );
}

export function LoadingState({
  className,
  description,
  title = "Loading",
  variant = "spinner",
}: {
  className?: string;
  description?: ReactNode;
  title?: ReactNode;
  variant?: "spinner" | "skeleton";
}) {
  if (variant === "skeleton") {
    return (
      <div className={cn("space-y-4", className)} aria-busy="true">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-4 w-80 max-w-full" />
        <div className="grid gap-3 md:grid-cols-2">
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-28 w-full" />
        </div>
      </div>
    );
  }

  return (
    <Empty
      className={cn("min-h-[220px] border-0 bg-transparent", className)}
      aria-busy="true"
    >
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <Loader2Icon className="animate-spin" />
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        {description ? (
          <EmptyDescription>{description}</EmptyDescription>
        ) : null}
      </EmptyHeader>
    </Empty>
  );
}

export function ErrorState({
  actionDisabled,
  actionIcon,
  actionLabel,
  className,
  detail,
  onAction,
  title,
}: {
  actionDisabled?: boolean;
  actionIcon?: ReactNode;
  actionLabel?: ReactNode;
  className?: string;
  detail?: ReactNode;
  onAction?: () => void;
  title: ReactNode;
}) {
  return (
    <Empty className={cn("min-h-[220px]", className)} role="alert">
      <EmptyHeader>
        <EmptyMedia
          variant="icon"
          className="border-destructive/25 bg-destructive/10 text-destructive"
        >
          <AlertCircleIcon />
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        {detail ? <EmptyDescription>{detail}</EmptyDescription> : null}
      </EmptyHeader>
      {onAction && actionLabel ? (
        <EmptyContent>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={actionDisabled}
            onClick={onAction}
          >
            {actionIcon ?? <RefreshCwIcon className="size-3.5" />}
            {actionLabel}
          </Button>
        </EmptyContent>
      ) : null}
    </Empty>
  );
}
