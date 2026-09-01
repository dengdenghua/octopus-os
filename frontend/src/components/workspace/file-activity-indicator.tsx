/* Implementation note. */
import {
  ChevronDownIcon,
  ChevronRightIcon,
  FileEditIcon,
  FileIcon,
  FilePlusIcon,
  FileXIcon,
} from "lucide-react";
import { useEffect, useState } from "react";

import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import type { FileOpEvent } from "@/core/observability/api";
import { useFileOpStream } from "@/core/observability/file-ops";
import { useI18n } from "@/core/i18n/hooks";
import { canAccessOperatorControlPlane } from "@/core/auth/control-plane-access";
import { cn } from "@/lib/utils";
import { useOptionalAuth } from "@/providers/AuthProvider";

interface Props {
  className?: string;
}

export function FileActivityIndicator({ className }: Props) {
  const { t } = useI18n();
  const auth = useOptionalAuth();
  const events = useFileOpStream({
    limit: 20,
    enabled: canAccessOperatorControlPlane(
      auth?.authStatus ?? null,
      auth?.user ?? null,
    ),
  });
  const [flashKey, setFlashKey] = useState<number | null>(null);
  const latest = events[events.length - 1];

  // Implementation note.
  useEffect(() => {
    if (events.length === 0) return;
    setFlashKey(Date.now());
  }, [events.length]);

  if (events.length === 0) return null;

  return (
    <HoverCard openDelay={200}>
      <HoverCardTrigger asChild>
        <button
          type="button"
          className={cn(
            "flex items-center gap-1 rounded-md px-2 py-1 text-xs",
            "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            "transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
            className,
          )}
          title={t.activityIndicators.recentFileActivity(events.length)}
          data-testid="file-activity-indicator"
        >
          <ActionIcon
            key={flashKey ?? "idle"}
            action={latest?.action ?? "write"}
            className={cn(
              "size-3.5",
              flashKey !== null &&
                "animate-learn-pulse text-[color:var(--primary)]",
            )}
            onAnimationEnd={() => setFlashKey(null)}
          />
          <span className="truncate max-w-[var(--text-truncate-md)]">
            {latest
              ? shortPath(latest.path)
              : t.activityIndicators.filesCount(events.length)}
          </span>
        </button>
      </HoverCardTrigger>
      <HoverCardContent align="start" className="w-80 sm:w-[480px] p-2 text-xs">
        <div className="font-medium mb-1.5 flex items-center justify-between">
          <span>{t.activityIndicators.fileActivityTitle(events.length)}</span>
          <span className="text-xs text-muted-foreground">
            {t.activityIndicators.realtimeLabel}
          </span>
        </div>
        <ul className="space-y-1 max-h-[60vh] overflow-y-auto">
          {[...events].reverse().map((e, i) => (
            <FileOpRow key={`${e.ts}-${i}`} event={e} />
          ))}
        </ul>
      </HoverCardContent>
    </HoverCard>
  );
}

function FileOpRow({ event }: { event: FileOpEvent }) {
  const [expanded, setExpanded] = useState(false);
  const hasDiff = !!event.diff && event.diff.length > 0;

  return (
    <li className="rounded border border-border-subtle bg-muted/20">
      <button
        type="button"
        onClick={() => hasDiff && setExpanded((v) => !v)}
        disabled={!hasDiff}
        className={cn(
          "w-full flex items-center gap-2 px-1.5 py-1 text-left",
          hasDiff
            ? "hover:bg-muted/50 cursor-pointer"
            : "cursor-default opacity-90",
        )}
      >
        {hasDiff ? (
          expanded ? (
            <ChevronDownIcon className="size-3 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRightIcon className="size-3 shrink-0 text-muted-foreground" />
          )
        ) : (
          <span className="size-3 shrink-0" />
        )}
        <ActionIcon
          action={event.action}
          className="size-3 text-muted-foreground shrink-0"
        />
        <span className="flex-1 font-mono text-xs truncate">{event.path}</span>
        <span className="text-xs text-muted-foreground tabular-nums shrink-0">
          {formatDelta(event.bytes_delta)}
        </span>
      </button>
      {expanded && event.diff && <DiffBlock diff={event.diff} />}
    </li>
  );
}

function DiffBlock({ diff }: { diff: string }) {
  return (
    <pre
      className={cn(
        "m-1 rounded border border-border-subtle bg-background px-2 py-1.5",
        "text-xs font-mono leading-snug whitespace-pre overflow-x-auto",
        "max-h-[var(--panel-height-md)] overflow-y-auto",
      )}
    >
      {diff.split("\n").map((line, i) => (
        <span
          key={i}
          className={cn(
            "block",
            line.startsWith("+") && !line.startsWith("+++") && "text-success",
            line.startsWith("-") &&
              !line.startsWith("---") &&
              "text-destructive",
            line.startsWith("@@") && "text-info dark:text-info",
            line.startsWith("+++") && "text-muted-foreground",
            line.startsWith("---") && "text-muted-foreground",
          )}
        >
          {line || "\u200b"}
        </span>
      ))}
    </pre>
  );
}

function ActionIcon({
  action,
  className,
  onAnimationEnd,
}: {
  action: FileOpEvent["action"];
  className?: string;
  onAnimationEnd?: () => void;
}) {
  const Icon =
    action === "create"
      ? FilePlusIcon
      : action === "delete"
        ? FileXIcon
        : action === "edit"
          ? FileEditIcon
          : FileIcon;
  return <Icon className={className} onAnimationEnd={onAnimationEnd} />;
}

function shortPath(p: string): string {
  const parts = p.split(/[/\\]/);
  if (parts.length <= 2) return p;
  return `.../${parts[parts.length - 1]}`;
}

function formatDelta(delta: number): string {
  if (delta === 0) return "·";
  const sign = delta > 0 ? "+" : "";
  const abs = Math.abs(delta);
  if (abs < 1024) return `${sign}${delta}B`;
  if (abs < 1024 * 1024) return `${sign}${(delta / 1024).toFixed(1)}K`;
  return `${sign}${(delta / 1024 / 1024).toFixed(1)}M`;
}
