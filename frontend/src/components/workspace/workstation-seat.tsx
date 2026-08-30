import { useState, type ReactNode } from "react";
import { BotIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export interface WorkstationSeatProps {
  /** Display name (codename / member name). Truncated when long. */
  name: string;
  /** Emoji or single-glyph avatar. Ignored when ``avatarUrl`` is set. */
  avatar?: string | null;
  /** Image avatar URL (takes precedence over ``avatar``). */
  avatarUrl?: string | null;
  /** Custom avatar node (e.g. an icon) used when no emoji/image is available. */
  avatarNode?: ReactNode;
  /** Letter to show when there is no avatar/image/node (e.g. a human initial). */
  fallbackInitial?: string;
  /** Render the small bot glyph badge over the avatar (AI members). */
  showBotBadge?: boolean;
  /** ``bg-*`` class for the trailing status dot. Omit to hide the dot. */
  dotClassName?: string;
  /** Accessible label / tooltip for the status dot. */
  dotLabel?: string;
  /** Inline badge after the name (e.g. 队长 / You). */
  badge?: ReactNode;
  /** Decorative trailing hint (e.g. an @ icon revealed on hover). Non-interactive. */
  trailing?: ReactNode;
  /** Native title tooltip (role / description / task). */
  title?: string;
  /** Active/focused styling. */
  selected?: boolean;
  /** Click handler — renders the seat as a button when provided. */
  onClick?: () => void;
  /** Constrain the name width (used in the horizontal dock). */
  compactName?: boolean;
  /** Avatar-only presentation for dense horizontal docks. */
  iconOnly?: boolean;
  /** Tiny caption below the avatar in avatar-only mode. */
  iconCaption?: string;
  className?: string;
  /** aria-label override for the button form. */
  ariaLabel?: string;
}

/**
 * The shared "工位" (workstation seat) atom rendered by BOTH the agent-mode
 * subagent dock and the team-mode roster, so a runtime subagent and a team
 * member read as the same kind of thing. Presentation-only: the status dot's
 * meaning (run-state vs presence) is supplied by the caller via
 * ``dotClassName`` so each surface keeps its own semantics.
 */
export function WorkstationSeat({
  name,
  avatar,
  avatarUrl,
  avatarNode,
  fallbackInitial,
  showBotBadge,
  dotClassName,
  dotLabel,
  badge,
  trailing,
  title,
  selected,
  onClick,
  compactName,
  iconOnly,
  iconCaption,
  className,
  ariaLabel,
}: WorkstationSeatProps) {
  const [failedAvatarUrl, setFailedAvatarUrl] = useState<string | null>(null);
  const showAvatarImage = Boolean(avatarUrl && avatarUrl !== failedAvatarUrl);
  const base = iconOnly
    ? iconCaption
      ? "group/seat relative inline-flex h-10 w-9 shrink-0 flex-col items-center justify-center gap-0.5 rounded-md border text-left transition-colors"
      : "group/seat relative inline-grid size-9 shrink-0 place-items-center rounded-md border text-left transition-colors"
    : "group/seat inline-flex min-w-0 items-center gap-2 rounded-md border px-2.5 py-1.5 text-left transition-colors";
  const tone = selected
    ? "border-foreground/25 bg-muted/45 text-foreground"
    : "border-transparent bg-transparent text-foreground hover:border-border-subtle hover:bg-muted/35";
  const statusText = dotLabel ? `${name} · ${dotLabel}` : name;
  const accessibleLabel = ariaLabel ?? statusText;

  const avatarElement = (
    <span
      className={cn(
        "relative grid shrink-0 place-items-center overflow-hidden rounded-lg bg-muted leading-none",
        iconOnly && iconCaption ? "size-6 text-xs" : "size-7 text-sm",
      )}
    >
      {showAvatarImage && avatarUrl ? (
        <img
          src={avatarUrl}
          alt={iconOnly ? "" : name}
          onError={() => setFailedAvatarUrl(avatarUrl ?? null)}
          className="size-full object-cover"
        />
      ) : avatar?.trim() ? (
        <span aria-hidden="true">{avatar}</span>
      ) : avatarNode ? (
        avatarNode
      ) : (
        <span className="text-xs font-semibold text-muted-foreground">
          {(fallbackInitial ?? name.charAt(0)).toUpperCase()}
        </span>
      )}
      {showBotBadge && (
        <span className="absolute -bottom-0.5 -right-0.5 grid size-3 place-items-center rounded-full bg-background">
          <BotIcon
            className="size-2 text-muted-foreground"
            aria-hidden="true"
          />
        </span>
      )}
    </span>
  );

  const iconStatusDot = dotClassName ? (
    <span
      className={cn(
        "absolute -right-0.5 -top-0.5 size-2.5 rounded-full ring-2 ring-background",
        dotClassName,
      )}
      aria-label={dotLabel}
      title={dotLabel}
    />
  ) : null;

  const inlineStatusDot = dotClassName ? (
    <span
      className={cn("size-1.5 shrink-0 rounded-full", dotClassName)}
      aria-label={dotLabel}
      title={dotLabel}
    />
  ) : null;

  const inner = iconOnly ? (
    <>
      {avatarElement}
      {iconStatusDot}
      {iconCaption ? (
        <span className="max-w-full truncate text-xs font-medium leading-none text-muted-foreground">
          {iconCaption}
        </span>
      ) : null}
    </>
  ) : (
    <>
      {avatarElement}
      <span
        className={cn(
          "truncate text-sm font-medium",
          compactName && "max-w-28",
        )}
      >
        {name}
      </span>
      {badge}
      {inlineStatusDot}
      {trailing}
    </>
  );

  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        title={title}
        aria-label={accessibleLabel}
        className={cn(base, tone, className)}
      >
        {inner}
      </button>
    );
  }
  return (
    <div
      title={title}
      aria-label={accessibleLabel}
      role={iconOnly ? "img" : undefined}
      className={cn(base, tone, className)}
    >
      {inner}
    </div>
  );
}
