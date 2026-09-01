import {
  SearchIcon,
  Loader2Icon,
  MessageSquareIcon,
  XIcon,
} from "lucide-react";
import { useState, useCallback, useEffect, useRef } from "react";
import { swallow } from "@/core/utils/log";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

interface SearchResult {
  thread_id: string;
  title: string;
  snippet: string;
  created_at: string;
  message_count: number;
}

export function SessionSearch({
  open,
  onClose,
  onSelectThread,
  className,
}: {
  open: boolean;
  onClose: () => void;
  onSelectThread: (threadId: string) => void;
  className?: string;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) {
      setQuery("");
      setResults([]);
    }
  }, [open]);

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

  const search = useCallback(async (q: string) => {
    if (!q.trim()) {
      setResults([]);
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/threads/search?q=${encodeURIComponent(q)}&limit=20`,
      );
      if (res.ok) {
        const data = await res.json();
        setResults(data.threads || data || []);
      }
    } catch (e) {
      swallow(e);
    }
    setLoading(false);
  }, []);

  const handleInput = useCallback(
    (value: string) => {
      setQuery(value);
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => search(value), 300);
    },
    [search],
  );

  if (!open) return null;

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
        className="bg-background w-full max-w-lg rounded-lg border shadow-2xl"
      >
        {/* Search input */}
        <div className="flex items-center gap-2 border-b px-4 py-3">
          <SearchIcon className="text-muted-foreground size-4 shrink-0" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => handleInput(e.target.value)}
            placeholder={t.common.search + "..."}
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
            className="text-muted-foreground hover:text-foreground"
          >
            <XIcon className="size-4" />
          </button>
        </div>

        {/* Results */}
        <div className="max-h-80 overflow-y-auto">
          {results.length === 0 && query && !loading && (
            <div className="text-muted-foreground py-8 text-center text-sm">
              No results found
            </div>
          )}
          {results.map((r) => (
            <button
              key={r.thread_id}
              onClick={() => {
                onSelectThread(r.thread_id);
                onClose();
              }}
              className="hover:bg-muted flex w-full items-start gap-3 px-4 py-3 text-left transition-colors"
            >
              <MessageSquareIcon className="text-muted-foreground mt-0.5 size-4 shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">
                  {r.title || t.sidebar.newChat}
                </div>
                {r.snippet && (
                  <div className="text-muted-foreground mt-0.5 line-clamp-2 text-xs">
                    {r.snippet}
                  </div>
                )}
                <div className="text-muted-foreground/60 mt-1 text-xs">
                  {r.message_count} messages
                  {r.created_at
                    ? ` · ${new Date(r.created_at).toLocaleDateString()}`
                    : ""}
                </div>
              </div>
            </button>
          ))}
        </div>

        {/* Footer */}
        <div className="text-muted-foreground border-t px-4 py-2 text-xs">
          <kbd className="rounded border px-1">Esc</kbd> to close
        </div>
      </div>
    </div>
  );
}
