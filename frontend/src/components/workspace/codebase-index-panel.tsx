import {
  AlertCircleIcon,
  CheckCircle2Icon,
  CodeIcon,
  DatabaseIcon,
  FileSearchIcon,
  HardDriveIcon,
  Loader2Icon,
  RefreshCwIcon,
  SearchIcon,
  SparklesIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { swallow } from "@/core/utils/log";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface IndexStatus {
  status: string;
  total_files: number;
  processed_files: number;
  total_chunks: number;
  embedded_chunks: number;
  skipped_files: number;
  progress_pct: number;
  elapsed_seconds: number;
  errors: string[];
  current_file: string;
  running: boolean;
}

interface IndexStats {
  total_files: number;
  total_chunks: number;
  total_embeddings: number;
  db_size_bytes: number;
  languages: Record<string, number>;
  chunk_types: Record<string, number>;
  last_indexed_at: string;
  config: {
    embedding_backend: string;
    embedding_model: string;
    chunk_max_tokens: number;
    search_top_k: number;
    auto_index: boolean;
  };
}

interface SearchResultItem {
  chunk_id: string;
  file_path: string;
  start_line: number;
  end_line: number;
  content: string;
  chunk_type: string;
  name: string;
  language: string;
  similarity: number;
  context_header: string;
}

interface SearchResponse {
  query: string;
  results: SearchResultItem[];
  total_results: number;
  elapsed_ms: number;
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

const api = {
  async status(): Promise<IndexStatus> {
    const res = await fetch(`${getBackendBaseURL()}/api/index/status`, {
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error("Failed to fetch index status");
    return res.json();
  },
  async stats(): Promise<IndexStats> {
    const res = await fetch(`${getBackendBaseURL()}/api/index/stats`, {
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error("Failed to fetch stats");
    return res.json();
  },
  async startIndex(force = false): Promise<void> {
    const res = await fetch(`${getBackendBaseURL()}/api/index/start`, {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ force }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || "Failed to start indexing");
    }
  },
  async rebuild(): Promise<void> {
    const res = await fetch(`${getBackendBaseURL()}/api/index/rebuild`, {
      method: "POST",
      headers: authHeaders(),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || "Failed to start rebuild");
    }
  },
  async clearIndex(): Promise<void> {
    const res = await fetch(`${getBackendBaseURL()}/api/index`, {
      method: "DELETE",
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error("Failed to clear index");
  },
  async search(query: string, topK = 10): Promise<SearchResponse> {
    const res = await fetch(`${getBackendBaseURL()}/api/index/search`, {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ query, top_k: topK }),
    });
    if (!res.ok) throw new Error("Failed to search");
    return res.json();
  },
};

// ---------------------------------------------------------------------------
// Helper: format bytes
// ---------------------------------------------------------------------------

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

// ---------------------------------------------------------------------------
// ProgressBar component
// ---------------------------------------------------------------------------

function ProgressBar({ pct, className }: { pct: number; className?: string }) {
  return (
    <div
      className={cn(
        "h-2 w-full overflow-hidden rounded-lg bg-muted",
        className,
      )}
    >
      <div
        className="h-full rounded-lg bg-primary transition-all duration-slow"
        style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// SearchResultCard component
// ---------------------------------------------------------------------------

function SearchResultCard({
  result,
  onOpenFile,
}: {
  result: SearchResultItem;
  onOpenFile?: (path: string, line: number) => void;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const similarityColor =
    result.similarity >= 0.7
      ? "text-success"
      : result.similarity >= 0.4
        ? "text-warning"
        : "text-muted-foreground";

  return (
    <div className="group rounded-lg border bg-card p-2 text-xs transition-colors hover:border-primary/30">
      <div className="flex items-start justify-between gap-2">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-start gap-1.5 text-left"
          onClick={() => onOpenFile?.(result.file_path, result.start_line)}
          aria-label={`Open ${result.file_path}:${result.start_line}`}
          title={`Open ${result.file_path}:${result.start_line}`}
        >
          <CodeIcon className="mt-0.5 size-3 shrink-0 text-muted-foreground" />
          <div className="min-w-0 flex-1">
            <div className="truncate font-medium text-foreground">
              {result.name !== "<module>" && result.name !== "<imports>"
                ? result.name
                : result.file_path.split("/").pop()}
            </div>
            <div className="truncate text-xs text-muted-foreground">
              {result.file_path}:{result.start_line}-{result.end_line}
            </div>
          </div>
        </button>
        <div className="flex shrink-0 items-center gap-1.5">
          <span className="rounded bg-muted px-1 py-0.5 text-xs uppercase text-muted-foreground">
            {result.chunk_type}
          </span>
          <span className="rounded bg-muted px-1 py-0.5 text-xs text-muted-foreground">
            {result.language}
          </span>
          <span
            className={cn("text-xs font-mono font-medium", similarityColor)}
          >
            {(result.similarity * 100).toFixed(1)}%
          </span>
        </div>
      </div>
      {result.context_header && (
        <div className="mt-1 truncate text-xs text-muted-foreground/70">
          {result.context_header}
        </div>
      )}
      <button
        type="button"
        className="mt-1 text-xs text-primary/70 hover:text-primary"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? t.codebaseIndex.hideCode : t.codebaseIndex.showCode}
      </button>
      {expanded && (
        <pre className="mt-1 max-h-48 overflow-auto rounded bg-muted/50 p-2 text-xs leading-tight">
          <code>{result.content}</code>
        </pre>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// StatsSection component
// ---------------------------------------------------------------------------

function StatsSection({ stats }: { stats: IndexStats | null }) {
  const { t } = useI18n();
  if (!stats) return null;

  const langEntries = Object.entries(stats.languages).sort(
    ([, a], [, b]) => b - a,
  );
  const typeEntries = Object.entries(stats.chunk_types).sort(
    ([, a], [, b]) => b - a,
  );

  return (
    <div className="space-y-3 text-xs">
      {/* Summary row */}
      <div className="grid grid-cols-3 gap-2">
        <div className="rounded border bg-card p-2 text-center">
          <div className="text-lg font-bold text-primary">
            {stats.total_files.toLocaleString()}
          </div>
          <div className="text-xs text-muted-foreground">
            {t.codebaseIndex.files}
          </div>
        </div>
        <div className="rounded border bg-card p-2 text-center">
          <div className="text-lg font-bold text-primary">
            {stats.total_chunks.toLocaleString()}
          </div>
          <div className="text-xs text-muted-foreground">
            {t.codebaseIndex.chunks}
          </div>
        </div>
        <div className="rounded border bg-card p-2 text-center">
          <div className="text-lg font-bold text-primary">
            {formatBytes(stats.db_size_bytes)}
          </div>
          <div className="text-xs text-muted-foreground">
            {t.codebaseIndex.dbSize}
          </div>
        </div>
      </div>

      {/* Config */}
      {stats.config && (
        <div className="rounded border bg-card p-2">
          <div className="mb-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            {t.codebaseIndex.configuration}
          </div>
          <div className="space-y-0.5 text-muted-foreground">
            <div>
              {t.codebaseIndex.backend}:{" "}
              <span className="text-foreground">
                {stats.config.embedding_backend}
              </span>
            </div>
            <div>
              {t.codebaseIndex.model}:{" "}
              <span className="text-foreground">
                {stats.config.embedding_model}
              </span>
            </div>
            <div>
              {t.codebaseIndex.chunkSize}:{" "}
              <span className="text-foreground">
                {stats.config.chunk_max_tokens} tokens
              </span>
            </div>
            <div>
              {t.codebaseIndex.autoIndex}:{" "}
              <span className="text-foreground">
                {stats.config.auto_index
                  ? t.codebaseIndex.on
                  : t.codebaseIndex.off}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Languages */}
      {langEntries.length > 0 && (
        <div className="rounded border bg-card p-2">
          <div className="mb-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            {t.codebaseIndex.languages}
          </div>
          <div className="flex flex-wrap gap-1">
            {langEntries.map(([lang, count]) => (
              <span
                key={lang}
                className="rounded bg-muted px-1.5 py-0.5 text-xs"
              >
                {lang} <span className="text-muted-foreground">{count}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Chunk types */}
      {typeEntries.length > 0 && (
        <div className="rounded border bg-card p-2">
          <div className="mb-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            {t.codebaseIndex.chunkTypes}
          </div>
          <div className="flex flex-wrap gap-1">
            {typeEntries.map(([type, count]) => (
              <span
                key={type}
                className="rounded bg-muted px-1.5 py-0.5 text-xs"
              >
                {type} <span className="text-muted-foreground">{count}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Last indexed */}
      {stats.last_indexed_at && (
        <div className="text-center text-xs text-muted-foreground">
          {t.codebaseIndex.lastIndexed}:{" "}
          {new Date(stats.last_indexed_at).toLocaleString()}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

type PanelTab = "search" | "status" | "stats";

export default function CodebaseIndexPanel({
  onOpenFile,
}: {
  onOpenFile?: (path: string, line: number) => void;
}) {
  const { t } = useI18n();
  const { confirm, confirmDialog } = useConfirmDialog();
  const [tab, setTab] = useState<PanelTab>("search");
  const [status, setStatus] = useState<IndexStatus | null>(null);
  const [stats, setStats] = useState<IndexStats | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResponse | null>(
    null,
  );
  const [searching, setSearching] = useState(false);
  const [indexing, setIndexing] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  // --- Load status and stats on mount ---
  const refreshStatus = useCallback(async () => {
    try {
      const [s, st] = await Promise.all([api.status(), api.stats()]);
      setStatus(s);
      setStats(st);
      setIndexing(s.running);
    } catch (e) {
      swallow(e);
    }
  }, []);

  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  // --- Poll while indexing ---
  useEffect(() => {
    if (indexing) {
      pollRef.current = setInterval(async () => {
        try {
          const s = await api.status();
          setStatus(s);
          if (!s.running) {
            setIndexing(false);
            // Refresh stats after indexing completes
            const st = await api.stats();
            setStats(st);
            toast.success(t.codebaseIndex.indexingComplete);
          }
        } catch (e) {
          swallow(e);
        }
      }, 1500);
      return () => {
        if (pollRef.current) clearInterval(pollRef.current);
      };
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [indexing, t.codebaseIndex.indexingComplete]);

  // --- Actions ---
  const handleStartIndex = useCallback(
    async (force = false) => {
      try {
        if (force) {
          await api.rebuild();
          toast.info(t.codebaseIndex.toastFullReindexStarted);
        } else {
          await api.startIndex();
          toast.info(t.codebaseIndex.toastIncrementalStarted);
        }
        setIndexing(true);
        setTab("status");
      } catch (err: unknown) {
        toast.error(
          err instanceof Error
            ? err.message
            : t.codebaseIndex.toastStartIndexingFailed,
        );
      }
    },
    [
      t.codebaseIndex.toastFullReindexStarted,
      t.codebaseIndex.toastIncrementalStarted,
      t.codebaseIndex.toastStartIndexingFailed,
    ],
  );

  const handleClear = useCallback(async () => {
    if (
      !(await confirm({
        title: t.codebaseIndex.clearIndexConfirmTitle,
        description: t.codebaseIndex.clearIndexConfirmDescription,
        confirmLabel: t.codebaseIndex.clearIndex,
      }))
    )
      return;
    try {
      await api.clearIndex();
      toast.success(t.codebaseIndex.toastIndexCleared);
      refreshStatus();
    } catch (err: unknown) {
      toast.error(
        err instanceof Error
          ? err.message
          : t.codebaseIndex.toastClearIndexFailed,
      );
    }
  }, [
    confirm,
    refreshStatus,
    t.codebaseIndex.clearIndexConfirmTitle,
    t.codebaseIndex.clearIndexConfirmDescription,
    t.codebaseIndex.clearIndex,
    t.codebaseIndex.toastIndexCleared,
    t.codebaseIndex.toastClearIndexFailed,
  ]);

  const handleSearch = useCallback(async () => {
    const q = searchQuery.trim();
    if (!q) return;
    setSearching(true);
    try {
      const res = await api.search(q, 15);
      setSearchResults(res);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Search failed");
    } finally {
      setSearching(false);
    }
  }, [searchQuery]);

  // Submit on Enter
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSearch();
    }
  };

  // --- Determine index state ---
  const isIndexed = (stats?.total_chunks ?? 0) > 0;
  const isRunning = indexing || (status?.running ?? false);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b px-3 py-2">
        <div className="flex items-center gap-2">
          <DatabaseIcon className="size-4 text-primary" />
          <span className="text-sm font-medium">{t.codebaseIndex.title}</span>
          {isIndexed && !isRunning && (
            <CheckCircle2Icon className="size-3.5 text-success" />
          )}
          {isRunning && (
            <Loader2Icon className="size-3.5 animate-spin text-primary" />
          )}
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => handleStartIndex(false)}
            disabled={isRunning}
            aria-label={t.codebaseIndex.indexIncremental}
            className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
            title={t.codebaseIndex.indexIncremental}
          >
            <SparklesIcon className="size-3.5" />
          </button>
          <button
            type="button"
            onClick={() => handleStartIndex(true)}
            disabled={isRunning}
            aria-label={t.codebaseIndex.rebuildFull}
            className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
            title={t.codebaseIndex.rebuildFull}
          >
            <RefreshCwIcon className="size-3.5" />
          </button>
          <button
            type="button"
            onClick={handleClear}
            disabled={isRunning || !isIndexed}
            aria-label={t.codebaseIndex.clearIndex}
            className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-destructive disabled:opacity-40"
            title={t.codebaseIndex.clearIndex}
          >
            <Trash2Icon className="size-3.5" />
          </button>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex border-b text-xs">
        {(["search", "status", "stats"] as PanelTab[]).map((t) => (
          <button
            type="button"
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "flex-1 px-3 py-1.5 text-center capitalize transition-colors",
              tab === t
                ? "border-b-2 border-primary font-medium text-primary"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-3">
        {/* ---- SEARCH TAB ---- */}
        {tab === "search" && (
          <div className="space-y-3">
            {/* Search bar */}
            <div className="flex items-center gap-1.5 rounded-lg border bg-card px-2 py-1.5">
              <SearchIcon className="size-3.5 shrink-0 text-muted-foreground" />
              <Input
                ref={searchInputRef}
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={t.codebaseIndex.searchPlaceholder}
                aria-label={t.codebaseIndex.searchPlaceholder}
                className="flex-1 border-0 bg-transparent text-xs outline-none placeholder:text-muted-foreground/50 focus-visible:ring-0"
              />
              {searching && (
                <Loader2Icon className="size-3 animate-spin text-primary" />
              )}
              {searchQuery && !searching && (
                <button
                  onClick={() => {
                    setSearchQuery("");
                    setSearchResults(null);
                    searchInputRef.current?.focus();
                  }}
                  className="text-muted-foreground hover:text-foreground"
                >
                  <XIcon className="size-3" />
                </button>
              )}
            </div>

            {/* Not indexed notice */}
            {!isIndexed && !isRunning && (
              <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed p-4 text-center text-xs text-muted-foreground">
                <FileSearchIcon className="size-8 text-muted-foreground/50" />
                <div>
                  <div className="font-medium text-foreground">
                    {t.codebaseIndex.notIndexed}
                  </div>
                  <div className="mt-0.5">{t.codebaseIndex.notIndexedHint}</div>
                </div>
                <button
                  type="button"
                  onClick={() => handleStartIndex(false)}
                  className="mt-1 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90"
                >
                  {t.codebaseIndex.startIndexing}
                </button>
              </div>
            )}

            {/* Search results */}
            {searchResults && (
              <div className="space-y-2">
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <span>
                    {searchResults.total_results} result
                    {searchResults.total_results !== 1 ? "s" : ""} for &quot;
                    {searchResults.query}&quot;
                  </span>
                  <span>{searchResults.elapsed_ms.toFixed(0)} ms</span>
                </div>
                {searchResults.results.length === 0 ? (
                  <div className="py-4 text-center text-xs text-muted-foreground">
                    {t.codebaseIndex.noMatchingCode}
                  </div>
                ) : (
                  <div className="space-y-1.5">
                    {searchResults.results.map((r) => (
                      <SearchResultCard
                        key={r.chunk_id}
                        result={r}
                        onOpenFile={onOpenFile}
                      />
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* ---- STATUS TAB ---- */}
        {tab === "status" && (
          <div className="space-y-3 text-xs">
            {isRunning && status ? (
              <>
                <div className="text-center">
                  <Loader2Icon className="mx-auto mb-2 size-6 animate-spin text-primary" />
                  <div className="font-medium">
                    {t.codebaseIndex.indexingInProgress}
                  </div>
                  <div className="mt-0.5 text-muted-foreground">
                    {status.current_file || t.codebaseIndex.preparing}
                  </div>
                </div>
                <ProgressBar pct={status.progress_pct} />
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div className="rounded border bg-card p-2">
                    <div className="text-muted-foreground">
                      {t.codebaseIndex.files}
                    </div>
                    <div className="font-medium">
                      {status.processed_files} / {status.total_files}
                    </div>
                  </div>
                  <div className="rounded border bg-card p-2">
                    <div className="text-muted-foreground">
                      {t.codebaseIndex.chunks}
                    </div>
                    <div className="font-medium">{status.total_chunks}</div>
                  </div>
                  <div className="rounded border bg-card p-2">
                    <div className="text-muted-foreground">
                      {t.codebaseIndex.embedded}
                    </div>
                    <div className="font-medium">{status.embedded_chunks}</div>
                  </div>
                  <div className="rounded border bg-card p-2">
                    <div className="text-muted-foreground">
                      {t.codebaseIndex.skipped}
                    </div>
                    <div className="font-medium">{status.skipped_files}</div>
                  </div>
                </div>
                <div className="text-center text-xs text-muted-foreground">
                  Elapsed: {status.elapsed_seconds.toFixed(1)}s | Progress:{" "}
                  {status.progress_pct.toFixed(1)}%
                </div>
                {status.errors.length > 0 && (
                  <div className="rounded border border-destructive/30 bg-destructive/5 p-2">
                    <div className="mb-1 flex items-center gap-1 text-destructive">
                      <AlertCircleIcon className="size-3" />
                      <span className="font-medium">
                        {t.codebaseIndex.errors(status.errors.length)}
                      </span>
                    </div>
                    <div className="max-h-24 overflow-y-auto text-xs text-destructive/80">
                      {status.errors.slice(-5).map((e, i) => (
                        <div key={i} className="truncate">
                          {e}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            ) : status?.status === "complete" ? (
              <div className="flex flex-col items-center gap-2 py-4">
                <CheckCircle2Icon className="size-8 text-success" />
                <div className="text-center">
                  <div className="font-medium text-foreground">
                    {t.codebaseIndex.indexingComplete}
                  </div>
                  <div className="mt-0.5 text-muted-foreground">
                    {status.total_chunks} chunks from {status.total_files} files
                  </div>
                  <div className="text-muted-foreground">
                    Completed in {status.elapsed_seconds.toFixed(1)}s
                  </div>
                </div>
              </div>
            ) : (
              <div className="flex flex-col items-center gap-2 py-4 text-muted-foreground">
                <HardDriveIcon className="size-8 text-muted-foreground/50" />
                <div className="text-center">
                  <div className="font-medium text-foreground">
                    {isIndexed
                      ? t.codebaseIndex.indexReady
                      : t.codebaseIndex.noIndex}
                  </div>
                  <div className="mt-0.5">
                    {isIndexed
                      ? t.codebaseIndex.indexReadyHint
                      : t.codebaseIndex.noIndexHint}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* ---- STATS TAB ---- */}
        {tab === "stats" && <StatsSection stats={stats} />}
      </div>
      {confirmDialog}
    </div>
  );
}
