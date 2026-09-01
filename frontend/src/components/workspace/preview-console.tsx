/**
 * PreviewConsole — bottom log panel for the live preview.
 *
 * Consumes the ``PreviewDiagnostic[]`` already captured by
 * ``LivePreviewPanel`` via postMessage from the preview iframe. Renders
 * a collapsible row per entry with a one-click "add to chat" action so
 * the user can hand a runtime error to the assistant without manual
 * copy-paste.
 */

import { useMemo, useState } from "react";
import {
  AlertCircleIcon,
  AlertTriangleIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  FilterIcon,
  InfoIcon,
  PlusIcon,
  Trash2Icon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useI18n } from "@/core/i18n/hooks";
import type { PreviewDiagnostic } from "./live-preview-panel";

export interface PreviewConsoleProps {
  diagnostics: PreviewDiagnostic[];
  onClear?: () => void;
  /** Optional — when wired, each row shows an "add to chat" button. */
  onSendToChat?: (diagnostic: PreviewDiagnostic) => void;
  className?: string;
}

type LevelFilter = "all" | "error" | "warning" | "info";

function levelIcon(level: PreviewDiagnostic["level"]): React.ReactNode {
  if (level === "error") {
    return <AlertCircleIcon className="size-3.5 text-destructive" />;
  }
  if (level === "warning") {
    return <AlertTriangleIcon className="size-3.5 text-warning" />;
  }
  return <InfoIcon className="size-3.5 text-muted-foreground" />;
}

function formatTime(ts: number): string {
  const d = new Date(ts);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function PreviewConsole({
  diagnostics,
  onClear,
  onSendToChat,
  className,
}: PreviewConsoleProps) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const [filter, setFilter] = useState<LevelFilter>("all");

  const visible = useMemo(() => {
    if (filter === "all") return diagnostics;
    return diagnostics.filter((d) => d.level === filter);
  }, [diagnostics, filter]);

  const hasEntries = diagnostics.length > 0;
  const hasError = diagnostics.some((d) => d.level === "error");

  return (
    <div
      className={cn(
        "border-t border-border-default bg-background/50 text-xs",
        className,
      )}
    >
      {/* Header row — always visible */}
      <div className="flex items-center gap-2 px-3 py-1.5">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          disabled={!hasEntries}
          className={cn(
            "flex items-center gap-1 text-xs font-medium transition-colors",
            hasError && "text-destructive",
            !hasError && hasEntries && "text-warning",
            !hasEntries && "text-muted-foreground",
            hasEntries && "hover:text-foreground cursor-pointer",
            !hasEntries && "cursor-default",
          )}
          title={expanded ? "Collapse" : "Expand"}
        >
          {hasEntries ? (
            expanded ? (
              <ChevronDownIcon className="size-3" />
            ) : (
              <ChevronUpIcon className="size-3" />
            )
          ) : null}
          <span>{t.codeMode.previewConsole}</span>
          <span className="tabular-nums whitespace-nowrap rounded bg-muted px-1 py-0.5 text-xs">
            {t.codeMode.previewConsoleCount(diagnostics.length)}
          </span>
        </button>

        <div className="ml-auto flex items-center gap-1">
          {hasEntries && (
            <div
              className="flex items-center gap-0.5 rounded bg-muted/50 p-0.5"
              title="Filter"
            >
              <FilterIcon className="size-3 text-muted-foreground ml-1" />
              {(["all", "error", "warning", "info"] as LevelFilter[]).map(
                (lvl) => (
                  <button
                    key={lvl}
                    type="button"
                    onClick={() => setFilter(lvl)}
                    className={cn(
                      "rounded px-1.5 text-xs capitalize transition-colors",
                      filter === lvl
                        ? "bg-background text-foreground shadow-[var(--shadow-xs)]"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {lvl}
                  </button>
                ),
              )}
            </div>
          )}
          {hasEntries && onClear && (
            <button
              type="button"
              onClick={onClear}
              className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
              title={t.codeMode.previewConsoleClear}
            >
              <Trash2Icon className="size-3" />
            </button>
          )}
        </div>
      </div>

      {/* Expanded log list */}
      {expanded && hasEntries && (
        <div className="max-h-48 overflow-y-auto border-t border-border-subtle">
          {visible.length === 0 ? (
            <div className="px-3 py-4 text-center text-muted-foreground text-xs">
              {t.codeMode.previewConsoleEmpty}
            </div>
          ) : (
            <ul className="divide-y divide-border/30">
              {visible.map((d) => (
                <li
                  key={d.id}
                  className="group flex items-start gap-2 px-3 py-1.5 hover:bg-muted/40"
                >
                  <span className="mt-0.5 shrink-0">{levelIcon(d.level)}</span>
                  <span className="text-muted-foreground tabular-nums mt-0.5 shrink-0 text-xs">
                    {formatTime(d.timestamp)}
                  </span>
                  <span className="min-w-0 flex-1 break-words text-foreground/90">
                    {d.message}
                    {d.stack && (
                      <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap rounded bg-muted/60 p-1.5 text-xs text-muted-foreground">
                        {d.stack}
                      </pre>
                    )}
                  </span>
                  {onSendToChat && (
                    <button
                      type="button"
                      onClick={() => onSendToChat(d)}
                      className={cn(
                        "shrink-0 self-start rounded border border-transparent px-1.5 py-0.5 text-xs opacity-0 transition-opacity",
                        "group-hover:opacity-100",
                        "hover:border-primary/40 hover:bg-primary/10 hover:text-primary",
                      )}
                      title={t.codeMode.previewConsoleAddToChat}
                    >
                      <PlusIcon className="size-3" />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
