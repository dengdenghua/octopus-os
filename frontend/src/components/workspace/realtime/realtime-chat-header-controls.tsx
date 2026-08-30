import type { ReactNode } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export interface RealtimeChatHeaderShareOptions {
  title: string;
  prompt?: string;
  summary?: string;
  footer?: string;
  onExportReplay?: () => void;
}

/** One header entry for every kind of member; internal/remote split in-panel. */
export function RealtimeChatHeaderMemberSurface({
  aiMembers,
  className,
}: {
  aiMembers?: ReactNode;
  className?: string;
}) {
  const { t } = useI18n();

  if (!aiMembers) return null;

  return (
    <div
      role="group"
      aria-label={t.chatInputBox.collaborators}
      data-slot="realtime-header-members"
      className={cn(
        "inline-flex h-[42px] max-w-full shrink-0 items-stretch shadow-none sm:h-8",
        "[&_[data-slot=task-collaborator-trigger]]:h-full [&_[data-slot=task-collaborator-trigger]]:rounded-md [&_[data-slot=task-collaborator-trigger]]:border-0",
        className,
      )}
    >
      <div className="min-w-0">{aiMembers}</div>
    </div>
  );
}

/** Keeps the three persistent header actions in one non-wrapping cluster. */
export function RealtimeChatHeaderActions({
  recording,
  workbench,
  share,
  className,
}: {
  recording?: ReactNode;
  workbench?: ReactNode;
  share?: ReactNode;
  className?: string;
}) {
  const { t } = useI18n();
  return (
    <div
      data-slot="realtime-header-actions"
      className={cn("flex shrink-0 items-center gap-1", className)}
    >
      {recording}
      <div
        role="group"
        aria-label={t.realtime.viewActions}
        data-slot="realtime-header-view-actions"
        className={cn(
          "inline-flex h-[42px] shrink-0 items-stretch gap-0.5 sm:h-8",
          "[&_[data-slot=share-menu-trigger]]:h-full [&_[data-slot=share-menu-trigger]]:rounded-md [&_[data-slot=share-menu-trigger]]:border-0",
          "[&_[data-state]]:h-full [&_[data-state]]:rounded-md [&_[data-state]]:border-0",
        )}
      >
        {workbench}
        {share ? <div className="shrink-0">{share}</div> : null}
      </div>
    </div>
  );
}
