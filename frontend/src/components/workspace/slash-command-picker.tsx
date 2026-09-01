import { useEffect, useMemo, useRef, useState } from "react";

import {
  listSlashCommands,
  type SlashCommand,
} from "@/core/slash-commands/api";
import { cn } from "@/lib/utils";

/**
 * Typeahead overlay for the chat composer. When the draft starts
 * with `/`, the parent renders this list filtered by what the user
 * has typed so far. Arrow keys + Enter handled by the parent
 * composer; this component only renders + emits picks.
 */

export interface SlashCommandPickerProps {
  /** Text after the leading `/` — e.g. "rev" matches "review-pr". */
  filter: string;
  /** Fired when the user (or the parent's keyboard handler) picks one. */
  onPick: (cmd: SlashCommand) => void;
  /** Active index for keyboard navigation, controlled by the parent. */
  activeIndex: number;
  /** Parent passes a setter so it can clamp to the visible list size. */
  onMatchesChange?: (matches: SlashCommand[]) => void;
  className?: string;
}

// One catalog fetch per browser session. Slash commands change
// rarely (file edits in ~/.echo/commands/) so we don't need a
// per-render refetch — refresh on app reload is fine.
let _cache: SlashCommand[] | null = null;
let _inflight: Promise<SlashCommand[]> | null = null;

async function loadCatalog(): Promise<SlashCommand[]> {
  if (_cache) return _cache;
  if (!_inflight) {
    _inflight = listSlashCommands()
      .then((res) => {
        _cache = res.commands || [];
        return _cache;
      })
      .catch(() => {
        _cache = [];
        return _cache;
      });
  }
  return _inflight;
}

export function SlashCommandPicker({
  filter,
  onPick,
  activeIndex,
  onMatchesChange,
  className,
}: SlashCommandPickerProps) {
  const [catalog, setCatalog] = useState<SlashCommand[]>(_cache ?? []);

  useEffect(() => {
    if (_cache) return;
    let cancelled = false;
    loadCatalog().then((cmds) => {
      if (!cancelled) setCatalog(cmds);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const matches = useMemo(
    () =>
      catalog
        .filter((c) =>
          filter ? c.name.toLowerCase().startsWith(filter.toLowerCase()) : true,
        )
        .slice(0, 8),
    [catalog, filter],
  );

  // Keep the latest callback in a ref so the notification effect does
  // not need to depend on an unstable parent callback.
  const onMatchesChangeRef = useRef(onMatchesChange);
  useEffect(() => {
    onMatchesChangeRef.current = onMatchesChange;
  }, [onMatchesChange]);

  // Notify parent so it can clamp activeIndex without us owning it.
  useEffect(() => {
    onMatchesChangeRef.current?.(matches);
  }, [matches]);

  if (matches.length === 0) return null;

  return (
    <div
      className={cn(
        "absolute bottom-full left-0 right-0 mb-2 max-h-64 overflow-y-auto",
        "rounded-lg border border-border-default bg-popover shadow-[var(--shadow-md)]",
        "text-xs z-50",
        className,
      )}
    >
      {matches.map((cmd, i) => {
        const active = i === activeIndex;
        return (
          <button
            key={`${cmd.source}:${cmd.name}`}
            type="button"
            onMouseDown={(e) => {
              // mousedown (not click) so the textarea doesn't lose
              // focus before the pick fires.
              e.preventDefault();
              onPick(cmd);
            }}
            className={cn(
              "flex w-full items-start gap-2 px-3 py-1.5 text-left",
              active
                ? "bg-accent text-accent-foreground"
                : "hover:bg-accent/50",
            )}
          >
            <span className="font-mono text-muted-foreground">/</span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="font-medium">{cmd.name}</span>
                {cmd.argument_hint && (
                  <span className="text-muted-foreground">
                    {cmd.argument_hint}
                  </span>
                )}
                <span className="ml-auto text-xs text-muted-foreground/70">
                  {cmd.source}
                </span>
              </div>
              {cmd.description && (
                <div className="text-xs text-muted-foreground truncate">
                  {cmd.description}
                </div>
              )}
            </div>
          </button>
        );
      })}
    </div>
  );
}
