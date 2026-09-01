import {
  AlertCircleIcon,
  CheckCircle2Icon,
  InfoIcon,
  TriangleAlertIcon,
  XIcon,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

/**
 * System banner. Full-width informational strip that sits
 * above the main content (below the header). Used for: update
 * available, auth expired, backend disconnected, etc.
 *
 * Unlike a toast, banners persist until dismissed or the condition clears.
 */

export type BannerTone = "info" | "success" | "warning" | "danger";

export interface BannerProps {
  tone?: BannerTone;
  icon?: LucideIcon;
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  onDismiss?: () => void;
  className?: string;
}

const TONE_ICONS: Record<BannerTone, LucideIcon> = {
  info: InfoIcon,
  success: CheckCircle2Icon,
  warning: TriangleAlertIcon,
  danger: AlertCircleIcon,
};

const TONE_STYLES: Record<BannerTone, string> = {
  info: "border-[color:color-mix(in_oklch,var(--primary)_25%,transparent)] bg-[color:color-mix(in_oklch,var(--primary)_6%,transparent)] text-foreground [&_[data-banner-icon]]:text-primary",
  success:
    "border-success/25 bg-success/50/[0.06] text-foreground [&_[data-banner-icon]]:text-success",
  warning:
    "border-warning/30 bg-warning/50/[0.08] text-foreground [&_[data-banner-icon]]:text-warning",
  danger:
    "border-destructive/30 bg-destructive/50/[0.07] text-foreground [&_[data-banner-icon]]:text-destructive",
};

export function Banner({
  tone = "info",
  icon,
  title,
  children,
  action,
  onDismiss,
  className,
}: BannerProps) {
  const { t } = useI18n();
  const Icon = icon ?? TONE_ICONS[tone];
  return (
    <div
      role={tone === "danger" || tone === "warning" ? "alert" : "status"}
      className={cn(
        "flex w-full items-start gap-3 rounded-xl border px-3.5 py-2.5",
        "backdrop-blur-[2px] transition-colors",
        TONE_STYLES[tone],
        className,
      )}
    >
      <Icon data-banner-icon className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1">
        {title && (
          <div className="text-sm font-medium leading-tight">{title}</div>
        )}
        {children && (
          <div
            className={cn("text-xs text-muted-foreground", title && "mt-0.5")}
          >
            {children}
          </div>
        )}
      </div>
      {action && <div className="shrink-0">{action}</div>}
      {onDismiss && (
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          onClick={onDismiss}
          aria-label={t.common.close}
          className="-mr-1 -mt-0.5 size-7 shrink-0 opacity-60 hover:opacity-100"
        >
          <XIcon className="size-3.5" />
        </Button>
      )}
    </div>
  );
}
