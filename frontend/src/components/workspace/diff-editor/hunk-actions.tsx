/**
 * Per-hunk action toolbar for the diff editor.
 *
 * Renders a floating toolbar over hunk headers with accept/reject buttons
 * and status indicators.
 */

import { CheckIcon, XIcon } from "lucide-react";
import { useCallback } from "react";

import { cn } from "@/lib/utils";
import { useI18n } from "@/core/i18n/hooks";

import type { DiffHunk } from "./utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface HunkActionsProps {
  hunk: DiffHunk;
  filePath: string;
  onAcceptHunk: (filePath: string, hunkId: string) => void;
  onRejectHunk: (filePath: string, hunkId: string) => void;
  className?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function HunkActions({
  hunk,
  filePath,
  onAcceptHunk,
  onRejectHunk,
  className,
}: HunkActionsProps) {
  const { t } = useI18n();

  const handleAccept = useCallback(() => {
    onAcceptHunk(filePath, hunk.id);
  }, [filePath, hunk.id, onAcceptHunk]);

  const handleReject = useCallback(() => {
    onRejectHunk(filePath, hunk.id);
  }, [filePath, hunk.id, onRejectHunk]);

  const isDecided = hunk.accepted !== null;

  return (
    <div className={cn("flex items-center gap-1", className)}>
      {isDecided ? (
        <span
          className={cn(
            "flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium",
            hunk.accepted
              ? "bg-success/15 text-success"
              : "bg-destructive/15 text-destructive",
          )}
        >
          {hunk.accepted ? (
            <>
              <CheckIcon className="size-3" />
              {t.diffEditor.accepted}
            </>
          ) : (
            <>
              <XIcon className="size-3" />
              {t.diffEditor.rejected}
            </>
          )}
        </span>
      ) : (
        <>
          <button
            onClick={handleAccept}
            className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium text-success transition-colors hover:bg-success/15 dark:text-success"
            title={t.diffEditor.hunkAccept}
          >
            <CheckIcon className="size-3" />
            {t.diffEditor.accept}
          </button>
          <button
            onClick={handleReject}
            className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium text-destructive transition-colors hover:bg-destructive/15 dark:text-destructive"
            title={t.diffEditor.hunkReject}
          >
            <XIcon className="size-3" />
            {t.diffEditor.reject}
          </button>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Hunk header component
// ---------------------------------------------------------------------------

interface HunkHeaderProps {
  hunk: DiffHunk;
  filePath: string;
  onAcceptHunk: (filePath: string, hunkId: string) => void;
  onRejectHunk: (filePath: string, hunkId: string) => void;
}

export function HunkHeader({
  hunk,
  filePath,
  onAcceptHunk,
  onRejectHunk,
}: HunkHeaderProps) {
  // Extract context info from header (function name, etc.)
  const contextMatch = hunk.header.match(/@@ .+? @@\s*(.*)/);
  const context = contextMatch?.[1] ?? "";

  return (
    <div className="group flex items-center justify-between bg-info/5 px-3 py-1 dark:bg-info/10">
      <div className="flex items-center gap-2 overflow-hidden">
        <span className="shrink-0 font-mono text-xs text-info dark:text-info">
          {hunk.header.split("@@").slice(0, 2).join("@@")}@@
        </span>
        {context && (
          <span className="truncate font-mono text-xs text-muted-foreground">
            {context}
          </span>
        )}
      </div>

      <div className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
        <HunkActions
          hunk={hunk}
          filePath={filePath}
          onAcceptHunk={onAcceptHunk}
          onRejectHunk={onRejectHunk}
        />
      </div>
    </div>
  );
}
