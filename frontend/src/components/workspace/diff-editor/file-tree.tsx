/**
 * Changed files tree sidebar for the multi-file diff editor.
 *
 * Groups changed files by directory, shows status icons/colors,
 * additions/deletions count, and supports search/filter/sort.
 */

import {
  ChevronDownIcon,
  ChevronRightIcon,
  FilePlusIcon,
  FileMinusIcon,
  FileEditIcon,
  FolderIcon,
  SearchIcon,
  ArrowUpDownIcon,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { cn } from "@/lib/utils";
import { useI18n } from "@/core/i18n/hooks";

import type { FileDiff, SortMode } from "./utils";
import { getFileName, groupFilesByDirectory, sortFiles } from "./utils";

// ---------------------------------------------------------------------------
// Status badge / icon helpers
// ---------------------------------------------------------------------------

const STATUS_CONFIG = {
  added: {
    icon: FilePlusIcon,
    color: "text-success",
    bg: "bg-success/10",
    label: "A",
  },
  modified: {
    icon: FileEditIcon,
    color: "text-warning",
    bg: "bg-warning/10",
    label: "M",
  },
  deleted: {
    icon: FileMinusIcon,
    color: "text-destructive",
    bg: "bg-destructive/10",
    label: "D",
  },
} as const;

function AcceptedBadge({ accepted }: { accepted: boolean | null }) {
  if (accepted === null) return null;
  return (
    <span
      className={cn(
        "ml-auto shrink-0 rounded px-1 py-px text-xs font-semibold uppercase leading-tight",
        accepted
          ? "bg-success/15 text-success"
          : "bg-destructive/15 text-destructive",
      )}
    >
      {accepted ? "ok" : "rej"}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface FileTreeProps {
  files: FileDiff[];
  selectedFile: string | null;
  onSelectFile: (filePath: string) => void;
  className?: string;
}

export function DiffFileTree({
  files,
  selectedFile,
  onSelectFile,
  className,
}: FileTreeProps) {
  const { t } = useI18n();
  const [search, setSearch] = useState("");
  const [sortMode, setSortMode] = useState<SortMode>("name");
  const [collapsedDirs, setCollapsedDirs] = useState<Set<string>>(new Set());

  // Cycle sort mode
  const cycleSortMode = useCallback(() => {
    setSortMode((prev) => {
      if (prev === "name") return "status";
      if (prev === "status") return "changes";
      return "name";
    });
  }, []);

  const sortLabel = useMemo(() => {
    switch (sortMode) {
      case "name":
        return t.diffEditor.sortByName;
      case "status":
        return t.diffEditor.sortByStatus;
      case "changes":
        return t.diffEditor.sortByChanges;
    }
  }, [sortMode, t]);

  // Filter + sort
  const filteredFiles = useMemo(() => {
    let result = files;
    if (search) {
      const lower = search.toLowerCase();
      result = result.filter((f) => f.filePath.toLowerCase().includes(lower));
    }
    return sortFiles(result, sortMode);
  }, [files, search, sortMode]);

  const groups = useMemo(
    () => groupFilesByDirectory(filteredFiles),
    [filteredFiles],
  );

  const toggleDir = (dir: string) => {
    setCollapsedDirs((prev) => {
      const next = new Set(prev);
      if (next.has(dir)) next.delete(dir);
      else next.add(dir);
      return next;
    });
  };

  // Stats
  const totalAdditions = files.reduce((s, f) => s + f.additions, 0);
  const totalDeletions = files.reduce((s, f) => s + f.deletions, 0);

  return (
    <div className={cn("flex h-full flex-col", className)}>
      {/* Header with stats */}
      <div className="flex items-center justify-between border-b px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {t.diffEditor.changedFiles}
          </span>
          <span className="flex items-center gap-1 rounded-lg bg-muted/70 px-1.5 py-0.5 text-xs font-medium text-muted-foreground">
            {files.length}
          </span>
        </div>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className="text-success">+{totalAdditions}</span>
          <span className="text-destructive">-{totalDeletions}</span>
        </div>
      </div>

      {/* Search + Sort */}
      <div className="flex items-center gap-1 border-b px-2 py-1.5">
        <div className="flex flex-1 items-center gap-1.5 rounded-lg bg-muted/40 px-2 py-1">
          <SearchIcon className="size-3 shrink-0 text-muted-foreground" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t.diffEditor.searchFiles}
            className="w-full bg-transparent text-xs outline-none placeholder:text-muted-foreground/60"
          />
        </div>
        <button
          onClick={cycleSortMode}
          className="flex items-center gap-1 rounded-lg p-1 text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
          title={sortLabel}
        >
          <ArrowUpDownIcon className="size-3" />
          <span className="text-xs uppercase">{sortLabel}</span>
        </button>
      </div>

      {/* File list */}
      <div className="flex-1 overflow-y-auto">
        {groups.length === 0 && (
          <div className="px-3 py-8 text-center text-xs text-muted-foreground">
            {t.diffEditor.noChanges}
          </div>
        )}

        {groups.map((group) => {
          const isCollapsed = collapsedDirs.has(group.directory);
          return (
            <div key={group.directory}>
              {/* Directory header */}
              {groups.length > 1 && (
                <button
                  onClick={() => toggleDir(group.directory)}
                  className="flex w-full items-center gap-1.5 px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted/30"
                >
                  {isCollapsed ? (
                    <ChevronRightIcon className="size-3 shrink-0" />
                  ) : (
                    <ChevronDownIcon className="size-3 shrink-0" />
                  )}
                  <FolderIcon className="size-3 shrink-0 text-warning" />
                  <span className="truncate font-mono text-xs">
                    {group.directory}
                  </span>
                  <span className="ml-auto shrink-0 text-xs">
                    {group.files.length}
                  </span>
                </button>
              )}

              {/* File entries */}
              {!isCollapsed &&
                group.files.map((file) => {
                  const config = STATUS_CONFIG[file.status];
                  const StatusIcon = config.icon;
                  const isSelected = selectedFile === file.filePath;

                  return (
                    <button
                      key={file.id}
                      onClick={() => onSelectFile(file.filePath)}
                      className={cn(
                        "flex w-full items-center gap-2 px-3 py-1.5 text-xs transition-colors",
                        "hover:bg-accent/50",
                        isSelected && "bg-accent text-accent-foreground",
                        groups.length > 1 && "pl-7",
                      )}
                    >
                      <StatusIcon
                        className={cn("size-3.5 shrink-0", config.color)}
                      />
                      <span className="truncate font-mono">
                        {getFileName(file.filePath)}
                      </span>

                      <AcceptedBadge accepted={file.accepted} />

                      <span className="ml-auto flex shrink-0 items-center gap-1 text-xs">
                        {file.additions > 0 && (
                          <span className="text-success">
                            +{file.additions}
                          </span>
                        )}
                        {file.deletions > 0 && (
                          <span className="text-destructive">
                            -{file.deletions}
                          </span>
                        )}
                      </span>
                    </button>
                  );
                })}
            </div>
          );
        })}
      </div>
    </div>
  );
}
