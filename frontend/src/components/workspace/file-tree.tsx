import {
  ChevronDownIcon,
  ChevronRightIcon,
  FileIcon,
  FolderIcon,
  FolderOpenIcon,
  RefreshCwIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { swallow } from "@/core/utils/log";
import type { components } from "@/core/api/openapi-types";
import { authHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

// Pulled from backend codegen (runtime/sensing/siphon/fs_router.py
// ``FsTreeEntry``) · ADR-004.
//
// We narrow ``type`` from the generated ``string`` to the actual
// ``"dir" | "file"`` union the backend only ever returns · the
// generated type can't express pydantic literals in openapi-typescript's
// current version · so the narrowing lives here where UI discrimination
// logic depends on it.
type FileEntry = Omit<components["schemas"]["FsTreeEntry"], "type"> & {
  type: "dir" | "file";
};

type GitStatus = "M" | "A" | "D" | "R";

/**
 * Event describing a file that was just written or edited by the Agent.
 * Consumed by FileTree to briefly highlight the affected row and scroll
 * it into view. See the `recentFileEvents` prop below.
 */
export interface FileTreeEvent {
  path: string;
  kind: "write" | "edit";
  at: number;
}

const FILE_ICONS: Record<string, string> = {
  ts: "text-info dark:text-info",
  tsx: "text-info dark:text-info",
  js: "text-warning",
  jsx: "text-warning",
  py: "text-success",
  json: "text-warning dark:text-warning",
  md: "text-muted-foreground",
  css: "text-chart-1 dark:text-chart-1",
  html: "text-chart-7 dark:text-chart-7",
  yaml: "text-destructive dark:text-destructive",
  yml: "text-destructive dark:text-destructive",
  toml: "text-destructive dark:text-destructive",
};

const treeCache = new Map<
  string,
  { entries: FileEntry[]; timestamp: number }
>();
const CACHE_TTL = 30_000;

// How long (ms) a file keeps its "just touched" glow after an event.
const HIGHLIGHT_TTL_MS = 2500;

function getFileColor(name: string) {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  return FILE_ICONS[ext] ?? "text-muted-foreground";
}

/**
 * Normalize a path for best-effort matching between event paths (which may
 * be absolute, relative, forward- or back-slashed) and the tree entry paths
 * returned by the backend (typically forward-slashed, relative to workDir).
 */
function normalizePath(p: string): string {
  return p.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

function pathsMatch(entryPath: string, eventPath: string): boolean {
  const a = normalizePath(entryPath);
  const b = normalizePath(eventPath);
  if (a === b) return true;
  return a.endsWith("/" + b) || b.endsWith("/" + a);
}

function parentPaths(path: string): string[] {
  const parts = path.split("/").filter(Boolean);
  return parts.slice(0, -1).map((_, idx) => parts.slice(0, idx + 1).join("/"));
}

function collapsedDirsFor(entries: FileEntry[]): Set<string> {
  return new Set(
    entries.filter((entry) => entry.type === "dir").map((entry) => entry.path),
  );
}

export function FileTree({
  workDir,
  threadId,
  className,
  onFileClick,
  recentFileEvents,
}: {
  workDir: string;
  threadId?: string | null;
  className?: string;
  onFileClick?: (path: string) => void;
  /**
   * Recent file-level tool events (write_file / str_replace / edit).
   * When a new event arrives the matching row is briefly highlighted and
   * scrolled into view. Optional — the component works fine without it.
   */
  recentFileEvents?: FileTreeEvent[];
}) {
  const { t } = useI18n();
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [gitStatuses, setGitStatuses] = useState<Map<string, GitStatus>>(
    new Map(),
  );
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [highlights, setHighlights] = useState<Map<string, number>>(new Map());
  const initializedRef = useRef(false);
  const initializedCollapsedKeyRef = useRef<string | null>(null);
  const rowRefs = useRef<Map<string, HTMLDivElement | null>>(new Map());
  const lastProcessedAtRef = useRef<number>(0);

  const fetchTree = useCallback(
    async (force = false) => {
      const cacheKey = `${threadId ?? ""}:${workDir}`;
      const applyEntries = (nextEntries: FileEntry[]) => {
        setEntries(nextEntries);
        if (initializedCollapsedKeyRef.current !== cacheKey) {
          initializedCollapsedKeyRef.current = cacheKey;
          setCollapsed(collapsedDirsFor(nextEntries));
        }
      };
      const cached = treeCache.get(cacheKey);
      if (!force && cached && Date.now() - cached.timestamp < CACHE_TTL) {
        applyEntries(cached.entries);
        setLoading(false);
        return;
      }

      setLoading(true);
      // Bound the tree fetch to 8s so a saturated backend can't leave the
      // explorer spinner running indefinitely — it's non-critical chrome,
      // late/fallback display is acceptable.
      const ac = new AbortController();
      const abortTimeout = window.setTimeout(() => ac.abort(), 8000);
      try {
        // Reduced depth 3→2: enough to show immediate project structure
        // without walking potentially-large subtrees (node_modules, .git
        // internals, build outputs). Deeper subdirs lazy-reveal on click.
        const params = new URLSearchParams({
          path: workDir,
          depth: "2",
          workspace_path: workDir,
        });
        if (threadId) {
          params.set("thread_id", threadId);
        }
        const res = await fetch(
          `${getBackendBaseURL()}/api/fs/tree?${params.toString()}`,
          { headers: authHeaders(), signal: ac.signal },
        );
        const data = await res.json();
        let result: FileEntry[] = [];
        if (Array.isArray(data)) {
          result = data;
        } else if (data && Array.isArray(data.entries)) {
          result = data.entries;
        }
        applyEntries(result);
        treeCache.set(cacheKey, { entries: result, timestamp: Date.now() });
      } catch (e) {
        swallow(e);
        // Aborted / error — fall back to empty so the UI can render.
        applyEntries([]);
      } finally {
        window.clearTimeout(abortTimeout);
        setLoading(false);
      }
    },
    [threadId, workDir],
  );

  const fetchGitStatus = useCallback(async () => {
    try {
      const params = new URLSearchParams({ path: workDir });
      const res = await fetch(
        `${getBackendBaseURL()}/api/git/status?${params.toString()}`,
        { headers: authHeaders() },
      );
      const data = await res.json();
      const next = new Map<string, GitStatus>();
      if (Array.isArray(data?.files)) {
        for (const file of data.files) {
          if (
            file &&
            typeof file.path === "string" &&
            typeof file.status === "string"
          ) {
            next.set(normalizePath(file.path), file.status as GitStatus);
          }
        }
      }
      setGitStatuses(next);
    } catch (e) {
      swallow(e);
      setGitStatuses(new Map());
    }
  }, [workDir]);

  useEffect(() => {
    if (!initializedRef.current) {
      initializedRef.current = true;
      fetchTree();
    } else {
      fetchTree();
    }
    void fetchGitStatus();
  }, [fetchTree, fetchGitStatus]);

  useEffect(() => {
    const handler = () => void fetchTree(true);
    window.addEventListener("echo:workspace-changed", handler);
    return () =>
      window.removeEventListener("echo:workspace-changed", handler);
  }, [fetchTree]);

  // Build a lookup: entry.path -> highlight expiry timestamp.
  // When a new event lands that matches a tree entry, set an expiry and
  // scroll the row into view. A tick timer clears expired entries.
  useEffect(() => {
    if (!recentFileEvents || recentFileEvents.length === 0) return;

    const newHighlights = new Map<string, number>();
    let shouldRefresh = false;
    let latestScrollTarget: string | null = null;
    let latestAt = lastProcessedAtRef.current;

    for (const ev of recentFileEvents) {
      if (ev.at <= lastProcessedAtRef.current) continue;
      // Find a matching tree entry (if any) — we tolerate abs/rel differences.
      const match = entries.find(
        (e) => e.type === "file" && pathsMatch(e.path, ev.path),
      );
      const key = match?.path ?? ev.path;
      newHighlights.set(key, Date.now() + HIGHLIGHT_TTL_MS);
      shouldRefresh = true;
      if (ev.at > latestAt) {
        latestAt = ev.at;
        latestScrollTarget = key;
      }
    }

    if (!shouldRefresh) return;

    lastProcessedAtRef.current = latestAt;

    setHighlights((prev) => {
      const next = new Map(prev);
      newHighlights.forEach((v: number, k: string) => {
        next.set(k, v);
      });
      return next;
    });

    if (latestScrollTarget) {
      setCollapsed((prev) => {
        const next = new Set(prev);
        parentPaths(latestScrollTarget!).forEach((path) => next.delete(path));
        return next;
      });
      // Defer to next tick so the row has a chance to mount if needed.
      setTimeout(() => {
        const el = rowRefs.current.get(latestScrollTarget!);
        if (el && typeof el.scrollIntoView === "function") {
          el.scrollIntoView({ block: "nearest" });
        }
      }, 0);
    }

    // Trigger a background refresh so newly-created files appear.
    void fetchTree(true);
  }, [recentFileEvents, entries, fetchTree]);

  // Sweep expired highlights every second.
  useEffect(() => {
    if (highlights.size === 0) return;
    const interval = setInterval(() => {
      const now = Date.now();
      setHighlights((prev) => {
        let changed = false;
        const next = new Map(prev);
        next.forEach((expires: number, k: string) => {
          if (expires <= now) {
            next.delete(k);
            changed = true;
          }
        });
        return changed ? next : prev;
      });
    }, 500);
    return () => clearInterval(interval);
  }, [highlights]);

  const highlightedKeys = useMemo(
    () => new Set(highlights.keys()),
    [highlights],
  );

  const toggleDir = (path: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const isHidden = (entry: FileEntry) => {
    const parts = entry.path.split("/");
    for (let i = 1; i < parts.length; i++) {
      const parentPath = parts.slice(0, i).join("/");
      if (collapsed.has(parentPath)) return true;
    }
    return false;
  };

  if (loading && entries.length === 0) {
    return (
      <div className={cn("flex items-center justify-center p-6", className)}>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <RefreshCwIcon className="size-3 animate-spin" />
          <span>{t.common.loading}</span>
        </div>
      </div>
    );
  }

  return (
    <div className={cn("overflow-y-auto text-xs", className)}>
      <div className="flex items-center justify-between px-3 pt-3 pb-1.5">
        <span className="text-muted-foreground font-semibold uppercase tracking-wider text-xs">
          {t.codeMode.explorer}
        </span>
        <button
          onClick={() => fetchTree(true)}
          className="text-muted-foreground hover:text-foreground p-1 rounded-lg hover:bg-muted/60 transition-colors"
          title={t.codeMode.refresh}
        >
          <RefreshCwIcon className={cn("size-3", loading && "animate-spin")} />
        </button>
      </div>
      {entries.map((entry) => {
        if (isHidden(entry)) return null;
        const isDir = entry.type === "dir";
        const isOpen = !collapsed.has(entry.path);
        const isHot = !isDir && highlightedKeys.has(entry.path);
        const gitStatus = !isDir
          ? gitStatuses.get(normalizePath(entry.path))
          : undefined;

        return (
          <div
            key={entry.path}
            ref={(el) => {
              if (el) rowRefs.current.set(entry.path, el);
              else rowRefs.current.delete(entry.path);
            }}
            className={cn(
              "flex items-center gap-1 px-2 py-[3px] hover:bg-accent/50 active:bg-accent/60 cursor-pointer rounded-lg mx-1 transition-colors group",
              isHot && "bg-primary/10 ring-1 ring-primary/40",
            )}
            style={{ paddingLeft: `${entry.depth * 14 + 8}px` }}
            role="button"
            tabIndex={0}
            aria-label={isDir ? t.fileTree.openFolderAria(entry.name) : t.fileTree.openFileAria(entry.name)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                if (isDir) {
                  toggleDir(entry.path);
                } else {
                  onFileClick?.(entry.path);
                }
              }
            }}
            onClick={() =>
              isDir ? toggleDir(entry.path) : onFileClick?.(entry.path)
            }
          >
            {isDir ? (
              <>
                {isOpen ? (
                  <ChevronDownIcon className="size-3 shrink-0 text-muted-foreground" />
                ) : (
                  <ChevronRightIcon className="size-3 shrink-0 text-muted-foreground" />
                )}
                {isOpen ? (
                  <FolderOpenIcon className="size-3.5 shrink-0 text-warning" />
                ) : (
                  <FolderIcon className="size-3.5 shrink-0 text-warning" />
                )}
              </>
            ) : (
              <>
                <span className="size-3 shrink-0" />
                <FileIcon
                  className={cn("size-3.5 shrink-0", getFileColor(entry.name))}
                />
              </>
            )}
            <span
              className={cn(
                "truncate group-hover:text-foreground",
                isDir ? "font-medium" : "text-muted-foreground",
                isHot && "text-foreground font-medium",
              )}
            >
              {entry.name}
            </span>
            {gitStatus && (
              <span
                className={cn(
                  "ml-auto shrink-0 rounded px-1 py-0.5 text-xs font-semibold",
                  gitStatus === "M" &&
                    "bg-warning/10 text-warning",
                  gitStatus === "A" &&
                    "bg-success/10 text-success",
                  gitStatus === "D" &&
                    "bg-destructive/10 text-destructive",
                  gitStatus === "R" &&
                    "bg-info/10 text-info dark:text-info",
                )}
              >
                {gitStatus}
              </span>
            )}
          </div>
        );
      })}
      {entries.length === 0 && !loading && (
        <div className="p-6 text-center text-muted-foreground text-xs">
          {t.fileTree.emptyDirectory}
        </div>
      )}
    </div>
  );
}
