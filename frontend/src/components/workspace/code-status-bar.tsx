import {
  GitBranchIcon,
  FolderIcon,
  ZapIcon,
  CircleDotIcon,
  LockIcon,
  LockOpenIcon,
} from "lucide-react";
import { useState, useEffect } from "react";
import { swallow } from "@/core/utils/log";
import { authHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import { joinPath } from "@/lib/path-utils";

interface CodeStatusBarProps {
  workDir: string;
  codeMode: string;
  modelName: string;
  isLoading?: boolean;
  /** Capability mode — when "fast" and isLoading, shows a 1-minute countdown
   *  badge. When "fast" and a completion time is supplied, shows the elapsed
   *  time badge (green if under 60s, amber otherwise). */
  capabilityMode?: string;
  /** Seconds elapsed since the current fast-mode turn started. Driven by the
   *  parent via a simple interval; we only render it. */
  fastElapsedSec?: number | null;
  /** When true, fast mode will hard-abort the run at 60s instead of just
   *  recolouring the badge. Drives the lock icon in the fast badge. */
  fastHardTimeout?: boolean;
  /** Toggle the hard-timeout preference. No-op if unset (badge stays
   *  read-only — parent doesn't need to support the feature). */
  onToggleFastHardTimeout?: () => void;
  className?: string;
}

export function CodeStatusBar({
  workDir,
  codeMode,
  modelName,
  isLoading,
  capabilityMode,
  fastElapsedSec,
  fastHardTimeout,
  onToggleFastHardTimeout,
  className,
}: CodeStatusBarProps) {
  const { t } = useI18n();
  const [gitBranch, setGitBranch] = useState<string | null>(() => {
    // Hydrate from sessionStorage so subsequent Code-page mounts in the same
    // session don't pay the fs/read round-trip again. The cache is keyed by
    // workDir so switching projects still refetches.
    try {
      const cached = sessionStorage.getItem(`git-branch:${workDir}`);
      return cached && cached !== "null" ? cached : null;
    } catch (e) {
      swallow(e);
      return null;
    }
  });

  useEffect(() => {
    // Defer the detection ~1.5s so it doesn't compete with the page's
    // critical API bursts (models, agents, thread history). The branch
    // name is a non-critical chrome decoration — showing it late is fine;
    // blocking initial paint on it is not.
    const ac = new AbortController();
    const timeoutId = window.setTimeout(() => {
      // Additionally bound the request itself to 3s so a stalled backend
      // can't wedge the status bar.
      const abortTimeout = window.setTimeout(() => ac.abort(), 3000);
      fetch(
        `${getBackendBaseURL()}/api/fs/read?path=${encodeURIComponent(joinPath(workDir, ".git/HEAD"))}&max_lines=1`,
        { headers: authHeaders(), signal: ac.signal },
      )
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (data?.content) {
            const match = data.content.match(/ref: refs\/heads\/(.+)/);
            const branch = match ? match[1].trim() : "detached";
            setGitBranch(branch);
            try {
              sessionStorage.setItem(`git-branch:${workDir}`, branch);
            } catch (e) {
              swallow(e, "storage");
            }
          }
        })
        .catch(() => {
          /* aborted / network failure — silent */
        })
        .finally(() => window.clearTimeout(abortTimeout));
    }, 1500);
    return () => {
      window.clearTimeout(timeoutId);
      ac.abort();
    };
  }, [workDir]);

  const shortDir = workDir.split(/[/\\]/).pop() || workDir;

  return (
    <div
      className={cn(
        "flex items-center justify-between border-t border-border-default workspace-panel-subtle px-3 py-0.5 font-mono text-xs text-muted-foreground",
        className,
      )}
    >
      <div className="flex items-center gap-3">
        {gitBranch && (
          <span className="flex items-center gap-1">
            <GitBranchIcon className="size-3" />
            {gitBranch}
          </span>
        )}
        <span className="flex items-center gap-1">
          <FolderIcon className="size-2.5" />
          {shortDir}
        </span>
      </div>

      <div className="flex items-center gap-3">
        {/* Fast-mode 1-minute-promise badge. Colour scheme:
              running + under 60s → primary
              running + over 60s  → amber
              not running + under 60s → emerald (we made the promise)
              not running + over 60s → muted (exceeded but done) */}
        {capabilityMode === "fast" && typeof fastElapsedSec === "number" && (
          <span className="flex items-center gap-1">
            <span
              className={cn(
                "flex items-center gap-1 font-semibold tabular-nums",
                isLoading
                  ? fastElapsedSec > 60
                    ? "text-warning"
                    : "text-primary"
                  : fastElapsedSec <= 60
                    ? "text-success"
                    : "text-muted-foreground",
              )}
              title={
                isLoading
                  ? fastElapsedSec > 60
                    ? t.codeMode.fastOver60s
                    : t.codeMode.fastRunning
                  : t.codeMode.fastCompleted
              }
            >
              <ZapIcon
                className={cn("size-2.5", isLoading && "animate-pulse")}
              />
              {isLoading
                ? t.codeMode.fastProgress(fastElapsedSec)
                : t.codeMode.fastDone(fastElapsedSec)}
            </span>
            {onToggleFastHardTimeout && (
              <button
                type="button"
                onClick={onToggleFastHardTimeout}
                title={
                  fastHardTimeout
                    ? t.codeMode.hardTimeoutOn
                    : t.codeMode.hardTimeoutOff
                }
                className={cn(
                  "flex items-center justify-center rounded-md p-0.5 transition-colors",
                  fastHardTimeout
                    ? "text-destructive hover:text-destructive"
                    : "text-muted-foreground/60 hover:text-foreground",
                )}
              >
                {fastHardTimeout ? (
                  <LockIcon className="size-2.5" />
                ) : (
                  <LockOpenIcon className="size-2.5" />
                )}
              </button>
            )}
          </span>
        )}
        {isLoading && (
          <span className="text-primary flex items-center gap-1">
            <CircleDotIcon className="size-2.5 animate-pulse" />
            {t.codeMode.running}
          </span>
        )}
        <span className="flex items-center gap-1">
          <ZapIcon className="size-2.5" />
          {codeMode}
        </span>
        <span className="text-muted-foreground/50">
          {modelName || t.codeMode.defaultModel}
        </span>
      </div>
    </div>
  );
}
