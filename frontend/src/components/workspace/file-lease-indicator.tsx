/**
 * File Lease Indicator · small inline widget for file-tree rows.
 *
 * Shows when a file is currently locked by another collaborator:
 *   - small holder avatar
 *   - "Xs left" countdown (auto-refreshes every 5s)
 *   - hover "Request takeover" button
 *
 * Mount this next to the file name in a tree row. If no lease applies
 * the component renders nothing.
 */

import { useEffect, useState } from "react";
import { LockIcon } from "lucide-react";
import { toast } from "sonner";

import { useI18n } from "@/core/i18n/hooks";
import { acquireLease } from "@/core/workspace/api";
import type { FileLease } from "@/core/workspace/types";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { currentActorId } from "@/core/auth/api";
import { cn } from "@/lib/utils";

interface FileLeaseIndicatorProps {
  workspaceId: string | null;
  lease: FileLease | null;
  /** Optional file path used when triggering a takeover. */
  filePath?: string;
  className?: string;
  /** Compact rendering for tight tree rows. */
  compact?: boolean;
}

const TICK_MS = 5000;

function avatarLetter(id: string): string {
  return (id[0] || "?").toUpperCase();
}

function remainingSeconds(expiresAt: string): number {
  const target = Date.parse(expiresAt);
  if (!Number.isFinite(target)) return 0;
  return (target - Date.now()) / 1000;
}

export function FileLeaseIndicator({
  workspaceId,
  lease,
  filePath,
  className,
  compact = true,
}: FileLeaseIndicatorProps) {
  const { t } = useI18n();
  const tr = t.remoteWorkspace.lease;
  // Re-render every TICK_MS so the countdown stays fresh. The lease
  // object itself is immutable; we just want the relative time to
  // tick down without re-fetching the list.
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!lease) return;
    const id = window.setInterval(() => setTick((n) => n + 1), TICK_MS);
    return () => window.clearInterval(id);
  }, [lease]);

  const [requesting, setRequesting] = useState(false);

  if (!lease) return null;

  const remaining = remainingSeconds(lease.expires_at);
  const isSelf = lease.holder_id === currentActorId();
  const holderLabel = tr.lockedBy(lease.holder_id);

  const handleRequestTakeover = async () => {
    if (!workspaceId || !filePath || requesting) return;
    setRequesting(true);
    try {
      // Acquire a fresh write lease to signal intent. The backend's
      // lease arbitration decides whether this immediately transfers
      // ownership or queues the request.
      await acquireLease(workspaceId, {
        file_path: filePath,
        holder_id: currentActorId(),
        kind: "write",
      });
      toast.success(tr.takeoverSent);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      toast.error(tr.takeoverFailed(message));
    } finally {
      setRequesting(false);
    }
  };

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-full border border-border-default bg-muted/40 px-1.5 py-0.5 text-xs font-medium text-muted-foreground",
            compact && "h-4",
            className,
          )}
          aria-label={holderLabel}
        >
          <LockIcon className="size-2.5 text-warning" />
          {!compact && (
            <Avatar className="size-3.5 rounded-full">
              <AvatarFallback className="rounded-full bg-muted text-micro font-semibold text-muted-foreground">
                {avatarLetter(lease.holder_id)}
              </AvatarFallback>
            </Avatar>
          )}
          {!compact && (
            <span className="max-w-[80px] truncate">{lease.holder_id}</span>
          )}
          <span className="tabular-nums">{tr.remaining(remaining)}</span>
        </span>
      </TooltipTrigger>
      <TooltipContent
        side="top"
        align="center"
        className="flex flex-col items-stretch gap-2 px-2 py-1.5 text-xs"
      >
        <div className="flex items-center gap-2">
          <Avatar className="size-5 rounded-full">
            <AvatarFallback className="rounded-full bg-muted text-xs font-semibold text-muted-foreground">
              {avatarLetter(lease.holder_id)}
            </AvatarFallback>
          </Avatar>
          <div className="min-w-0">
            <div className="truncate font-medium text-foreground">
              {holderLabel}
            </div>
            <div className="text-xs text-muted-foreground">
              {tr.remaining(remaining)}
            </div>
          </div>
        </div>
        {!isSelf && workspaceId && filePath && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 w-full text-xs"
            disabled={requesting}
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              void handleRequestTakeover();
            }}
          >
            {tr.requestTakeover}
          </Button>
        )}
      </TooltipContent>
    </Tooltip>
  );
}

/** Convenience wrapper that gates rendering on whether a lease exists. */
export function FileLeaseBadge({
  workspaceId: _workspaceId,
  lease,
  filePath: _filePath,
  className,
}: Omit<FileLeaseIndicatorProps, "compact">) {
  const { t } = useI18n();
  if (!lease) return null;
  return (
    <Badge
      variant="outline"
      className={cn(
        "gap-1 px-1 py-0 text-xs font-medium text-muted-foreground",
        className,
      )}
      aria-label={t.remoteWorkspace.lease.locked}
    >
      <LockIcon className="size-2.5 text-warning" />
      <span className="max-w-[60px] truncate">{lease.holder_id}</span>
    </Badge>
  );
}
