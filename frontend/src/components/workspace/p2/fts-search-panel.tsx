/**
 * Full-Text Search Panel Component
 *
 * Enhanced search panel using FTS (Full-Text Search) with SQLite FTS5.
 * Replaces the basic substring search with semantic search capabilities.
 */

import {
  SearchIcon,
  Loader2Icon,
  MessageSquareIcon,
  XIcon,
  CalendarIcon,
} from "lucide-react";
import { useEffect, useRef } from "react";
import { useThreadSearch } from "@/core/api/p2-hooks";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

interface FTSSearchPanelProps {
  open: boolean;
  onClose: () => void;
  onSelectThread: (threadId: string) => void;
  className?: string;
  agent_id?: string;
  team_id?: string;
}

export function FTSSearchPanel({
  open,
  onClose,
  onSelectThread,
  className,
  agent_id,
  team_id,
}: FTSSearchPanelProps) {
  const { t } = useI18n();
  const inputRef = useRef<HTMLInputElement>(null);

  const { query, setQuery, results, loading, error, clear } = useThreadSearch({
    debounceMs: 300,
    minQueryLength: 2,
    agent_id,
    team_id,
  });

  // Focus input when panel opens
  useEffect(() => {
    if (open) {
      inputRef.current?.focus();
    }
  }, [open]);

  // Clear state when panel closes
  useEffect(() => {
    if (!open) {
      clear();
    }
  }, [open, clear]);

  // Handle Escape key
  useEffect(() => {
    if (!open) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  const showEmpty = results.length === 0 && query.trim().length >= 2 && !loading;
  const showResults = results.length > 0;

  return (
    <div
      className={cn(
        "fixed inset-0 z-[100] flex items-start justify-center bg-black/50 pt-[20vh]",
        className,
      )}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t.common.search}
        className="bg-background w-full max-w-2xl rounded-lg border shadow-2xl"
      >
        {/* Search input */}
        <div className="flex items-center gap-2 border-b px-4 py-3">
          <SearchIcon className="text-muted-foreground size-4 shrink-0" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search conversations (full-text)..."
            aria-label={t.common.search}
            className="placeholder:text-muted-foreground flex-1 bg-transparent text-sm outline-none"
            autoFocus
          />
          {loading && (
            <Loader2Icon className="text-muted-foreground size-4 animate-spin" />
          )}
          <button
            onClick={onClose}
            aria-label={t.common.close}
            className="text-muted-foreground hover:text-foreground transition-colors"
          >
            <XIcon className="size-4" />
          </button>
        </div>

        {/* Results */}
        <div className="max-h-[60vh] overflow-y-auto">
          {/* Error state */}
          {error && (
            <div className="text-destructive py-8 text-center text-sm">
              Search error: {error.message}
            </div>
          )}

          {/* Empty state */}
          {showEmpty && !error && (
            <div className="text-muted-foreground py-8 text-center text-sm">
              No results found for &ldquo;{query}&rdquo;
            </div>
          )}

          {/* Results list */}
          {showResults && (
            <div className="divide-y">
              {results.map((result) => (
                <button
                  key={result.thread_id}
                  onClick={() => {
                    onSelectThread(result.thread_id);
                    onClose();
                  }}
                  className="hover:bg-muted flex w-full items-start gap-3 px-4 py-3 text-left transition-colors"
                >
                  <MessageSquareIcon className="text-muted-foreground mt-1 size-4 shrink-0" />

                  <div className="min-w-0 flex-1 space-y-1.5">
                    {/* Title */}
                    <div className="flex items-start justify-between gap-2">
                      <div className="truncate text-sm font-medium">
                        {result.title || t.sidebar.newChat}
                      </div>
                      <div className="text-muted-foreground/60 shrink-0 text-xs">
                        {result.rank.toFixed(1)}
                      </div>
                    </div>

                    {/* Snippet */}
                    {result.snippet && (
                      <div className="text-muted-foreground line-clamp-2 text-xs leading-relaxed">
                        {result.snippet}
                      </div>
                    )}

                    {/* Metadata */}
                    <div className="text-muted-foreground/70 flex items-center gap-3 text-xs">
                      <span className="flex items-center gap-1">
                        <CalendarIcon className="size-3" />
                        {new Date(result.created_at).toLocaleDateString()}
                      </span>
                      {result.updated_at && result.updated_at !== result.created_at && (
                        <span className="flex items-center gap-1">
                          Updated {new Date(result.updated_at).toLocaleDateString()}
                        </span>
                      )}
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="text-muted-foreground flex items-center justify-between border-t px-4 py-2 text-xs">
          <div>
            {showResults && (
              <span>
                {results.length} result{results.length !== 1 ? "s" : ""}
              </span>
            )}
          </div>
          <div>
            <kbd className="rounded border px-1.5 py-0.5">Esc</kbd> to close
          </div>
        </div>
      </div>
    </div>
  );
}
