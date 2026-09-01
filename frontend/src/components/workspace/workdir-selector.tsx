import {
  CheckIcon,
  ChevronDownIcon,
  CloudIcon,
  DatabaseIcon,
  FolderIcon,
  FolderOpenIcon,
  HardDriveIcon,
  Loader2Icon,
  ServerIcon,
  TerminalIcon,
  type LucideIcon,
} from "lucide-react";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

import { swallow } from "@/core/utils/log";
import { authHeaders } from "@/core/auth/api";
import type { components } from "@/core/api/openapi-types";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import {
  listWorkspaces,
  type MountType,
  type Workspace,
} from "@/core/workspace/api";
import { pickLocalDirectory } from "@/core/workspace/pick-local-directory";
import { basename, isAbsolutePath, joinPath } from "@/lib/path-utils";
import { cn } from "@/lib/utils";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/providers/AuthProvider";
import { useFeatureFlags } from "@/hooks/use-feature-flags";

interface WorkDirSelectorProps {
  workDir: string;
  onWorkDirChange?: (dir: string) => void;
  lockToCurrentThread?: boolean;
  onOpenWorkDirInNewTask?: (dir: string) => void;
  className?: string;
  variant?: "default" | "muted";
  chromeless?: boolean;
  /** When true, adds a "Remote mount" tab to the dropdown. */
  enableRemoteTab?: boolean;
  /** Active remote workspace id, if the thread is bound to one. */
  workspaceId?: string | null;
  /** Fired when the user picks a remote workspace in the Remote tab. */
  onWorkspaceIdChange?: (workspaceId: string) => void;
}

type FsTreeEntry = components["schemas"]["FsTreeEntry"];
const RECENT_WORKDIRS_KEY = "echo:recentWorkdirs";
const MAX_RECENT_WORKDIRS = 6;
const MENU_WIDTH = 360;
const MENU_MARGIN = 12;

// Remote mount type → lucide icon. Mirrors the WorkspaceSwitcher palette.
const MOUNT_TYPE_ICON: Record<MountType, LucideIcon> = {
  local: HardDriveIcon,
  smb: ServerIcon,
  nfs: ServerIcon,
  webdav: CloudIcon,
  sftp: TerminalIcon,
  s3: DatabaseIcon,
};

function readRecentWorkdirs(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(RECENT_WORKDIRS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed)
      ? dedupePaths(
          parsed.filter(
            (item): item is string =>
              typeof item === "string" && item.trim().length > 0,
          ),
        )
      : [];
  } catch (e) {
    swallow(e);
    return [];
  }
}

function writeRecentWorkdirs(paths: string[]) {
  if (typeof window === "undefined") return;
  localStorage.setItem(
    RECENT_WORKDIRS_KEY,
    JSON.stringify(dedupePaths(paths).slice(0, MAX_RECENT_WORKDIRS)),
  );
}

function normalizePathKey(path: string): string {
  const normalized = path.trim().replace(/\\/g, "/").replace(/\/+$/, "");
  return (normalized || path.trim()).toLowerCase();
}

function dedupePaths(paths: string[]): string[] {
  const seen = new Set<string>();
  return paths.filter((path) => {
    const key = normalizePathKey(path);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function dedupeDirEntries(entries: FsTreeEntry[]): FsTreeEntry[] {
  const seen = new Set<string>();
  return entries.filter((entry) => {
    const key = normalizePathKey(entry.path || entry.name);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function parentDir(path: string): string | null {
  const normalized = path.replace(/[\\/]+$/, "");
  const parts = normalized.split(/[\\/]/);
  if (parts.length <= 1) return null;
  if (parts.length === 2 && /^[A-Za-z]:$/.test(parts[0] || "")) {
    return `${parts[0]}/`;
  }
  parts.pop();
  return parts.join("/");
}

// Stable hash → palette index. Same folder name always lands on the
// same color across reloads, so the avatar tiles act as a visual id.
const AVATAR_PALETTE: Array<{ bg: string; fg: string }> = [
  { bg: "bg-destructive/15", fg: "text-destructive" },
  { bg: "bg-warning/15", fg: "text-warning" },
  { bg: "bg-success/15", fg: "text-success" },
  { bg: "bg-info/15", fg: "text-info dark:text-info" },
  { bg: "bg-chart-1/15", fg: "text-chart-1 dark:text-chart-1" },
  { bg: "bg-chart-3/15", fg: "text-chart-3 dark:text-chart-3" },
];

function avatarTile(path: string): { bg: string; fg: string; letter: string } {
  const name = basename(path) || path;
  let h = 0;
  for (let i = 0; i < name.length; i++) {
    h = (h * 31 + name.charCodeAt(i)) >>> 0;
  }
  const palette = AVATAR_PALETTE[h % AVATAR_PALETTE.length]!;
  return { ...palette, letter: (name[0] || "?").toUpperCase() };
}

function browseEntryPath(base: string, entryPath: string): string {
  return base ? joinPath(base, entryPath) : entryPath;
}

function entryDisplayName(entry: FsTreeEntry): string {
  const fromPath = basename(entry.path.replace(/[\\/]+$/, ""));
  return fromPath || entry.name || entry.path;
}

// Kept self-contained (not in the shared i18n bundle) so this touch-up stays
// decoupled from concurrently-edited locale files.
const WEB_PICKER_HINT: Record<"zh" | "en" | "ja" | "ko", string> = {
  zh: "未能打开系统文件夹选择器。你仍可选择最近使用的工作区，或在下方粘贴完整路径。",
  en: "The system folder picker could not be opened. Choose a recent workspace or paste its full path below.",
  ja: "システムのフォルダ選択を開けませんでした。最近のワークスペースを選ぶか、完全なパスを貼り付けてください。",
  ko: "시스템 폴더 선택기를 열 수 없습니다. 최근 작업 공간을 선택하거나 전체 경로를 붙여넣으세요.",
};

const LOCKED_WORKDIR_TEXT: Record<
  "zh" | "en" | "ja" | "ko",
  { triggerTitle: string; hint: string; openFolder: string }
> = {
  zh: {
    triggerTitle: "当前任务已绑定工作区",
    hint: "当前对话已绑定这个工作区。选择其他工作区会打开一个新任务，避免上下文和权限混在一起。",
    openFolder: "在新任务打开其他工作区",
  },
  en: {
    triggerTitle: "Current task is bound to this workspace",
    hint: "This conversation is bound to its workspace. Choosing another workspace opens a new task so context and permissions stay separate.",
    openFolder: "Open another workspace in a new task",
  },
  ja: {
    triggerTitle: "このタスクは現在のワークスペースに固定されています",
    hint: "この会話は現在のワークスペースに固定されています。別のワークスペースは新しいタスクで開き、文脈と権限を分けます。",
    openFolder: "別のワークスペースを新しいタスクで開く",
  },
  ko: {
    triggerTitle: "현재 작업은 이 작업 공간에 고정되어 있습니다",
    hint: "이 대화는 현재 작업 공간에 고정되어 있습니다. 다른 작업 공간은 새 작업으로 열어 컨텍스트와 권한을 분리합니다.",
    openFolder: "다른 작업 공간을 새 작업으로 열기",
  },
};

function webPickerHint(locale: string): string {
  const lang = (locale || "en").slice(0, 2).toLowerCase();
  if (lang === "zh") return WEB_PICKER_HINT.zh;
  if (lang === "ja") return WEB_PICKER_HINT.ja;
  if (lang === "ko") return WEB_PICKER_HINT.ko;
  return WEB_PICKER_HINT.en;
}

function lockedWorkdirText(locale: string) {
  const lang = (locale || "en").slice(0, 2).toLowerCase();
  if (lang === "zh") return LOCKED_WORKDIR_TEXT.zh;
  if (lang === "ja") return LOCKED_WORKDIR_TEXT.ja;
  if (lang === "ko") return LOCKED_WORKDIR_TEXT.ko;
  return LOCKED_WORKDIR_TEXT.en;
}

export function WorkDirSelector({
  workDir,
  onWorkDirChange,
  lockToCurrentThread = false,
  onOpenWorkDirInNewTask,
  className,
  variant = "default",
  chromeless = false,
  enableRemoteTab = false,
  workspaceId,
  onWorkspaceIdChange,
}: WorkDirSelectorProps) {
  const isMutedVariant = variant === "muted";
  const { t, locale } = useI18n();
  const { authStatus, isAuthenticated, isLoading: authLoading } = useAuth();
  const featureFlags = useFeatureFlags();
  const remoteWorkspaceEnabled =
    !featureFlags.loading && featureFlags.isOn("ui.remote_workspace");
  const trRemote = t.remoteWorkspace;
  const [isPicking, setIsPicking] = useState(false);
  const [showMenu, setShowMenu] = useState(false);
  // ``browsePath`` drives the in-menu folder browser. When the user
  // hasn't chosen anything yet we seed it from the most recently used
  // directory so the picker isn't pointing at an empty string (which
  // the backend's /api/fs/tree rejects).
  const [browsePath, setBrowsePath] = useState(() => {
    if (workDir) return workDir;
    if (typeof window === "undefined") return "";
    try {
      const recent = readRecentWorkdirs();
      if (recent.length > 0) return recent[0]!;
    } catch (e) {
      swallow(e);
    }
    return "";
  });
  const [browseEntries, setBrowseEntries] = useState<FsTreeEntry[]>([]);
  const [browseLoading, setBrowseLoading] = useState(false);
  const [recentWorkdirs, setRecentWorkdirs] = useState<string[]>(() =>
    readRecentWorkdirs(),
  );
  const [manualPath, setManualPath] = useState(workDir);
  const [isBrowserOpen, setBrowserOpen] = useState(
    () => !isMutedVariant && !workDir,
  );
  // True when the user just hit the "pick folder" CTA but no Electron bridge
  // is present (i.e. running in a plain browser). We surface the manual input
  // and a one-line hint instead of silently doing nothing. Resets once the
  // user enters a path or closes the menu.
  const [noBridgeHint, setNoBridgeHint] = useState(false);
  // Remote workspace list (loaded when the Remote tab is enabled and the
  // menu opens). Stored at the component level so the second open is instant.
  const [remoteWorkspaces, setRemoteWorkspaces] = useState<Workspace[]>([]);
  const [remoteLoading, setRemoteLoading] = useState(false);
  const [remoteError, setRemoteError] = useState<string | null>(null);
  // Active tab inside the dropdown — defaults to "local" so existing
  // users see the same UI as before. We only switch to "remote" if
  // the parent has bound the thread to a workspace_id already.
  const [menuTab, setMenuTab] = useState<"local" | "remote">(
    workspaceId ? "remote" : "local",
  );
  const containerRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const manualInputRef = useRef<HTMLInputElement>(null);
  const pendingBrowserPickedNameRef = useRef("");
  const folderName = useMemo(
    () => (workDir ? basename(workDir) : ""),
    [workDir],
  );
  const lockedCopy = lockedWorkdirText(locale);
  const isEmpty = !workDir;
  const isWorkDirLocked = lockToCurrentThread;
  const emptyTriggerLabel = isMutedVariant
    ? t.codeMode.personalSpace
    : t.codeMode.chooseWorkspaceFolder;
  const triggerLabel =
    !isEmpty && isMutedVariant
      ? folderName
      : isEmpty
        ? emptyTriggerLabel
        : folderName;
  const folderPickerLabel = isWorkDirLocked
    ? lockedCopy.openFolder
    : isMutedVariant
      ? t.codeMode.chooseWorkspaceFolder
      : t.codeMode.openFolderCta;
  const triggerTitle = isEmpty
    ? emptyTriggerLabel
    : isWorkDirLocked
      ? `${lockedCopy.triggerTitle}: ${workDir}`
      : `${t.codeMode.chooseWorkspaceFolder}: ${workDir}`;
  const _menuToggleTitle = isMutedVariant
    ? t.codeMode.chooseWorkspaceFolder
    : t.codeMode.recentWorkspaces;
  const emptyTriggerClass = isMutedVariant
    ? "text-muted-foreground"
    : "text-muted-foreground";
  const activeTriggerClass = isMutedVariant
    ? "text-foreground"
    : "text-foreground";

  const applyWorkDir = useCallback(
    (dir: string) => {
      const next = dir.trim();
      if (!next || !isAbsolutePath(next)) return;
      if (isWorkDirLocked) {
        if (normalizePathKey(next) !== normalizePathKey(workDir)) {
          onOpenWorkDirInNewTask?.(next);
        }
        setShowMenu(false);
        setNoBridgeHint(false);
        return;
      }
      onWorkDirChange?.(next);
      setBrowsePath(next);
      setRecentWorkdirs((prev) => {
        const merged = dedupePaths([next, ...prev]).slice(
          0,
          MAX_RECENT_WORKDIRS,
        );
        writeRecentWorkdirs(merged);
        return merged;
      });
      setShowMenu(false);
      setNoBridgeHint(false);
    },
    [isWorkDirLocked, onOpenWorkDirInNewTask, onWorkDirChange, workDir],
  );

  const clearWorkDir = useCallback(() => {
    onWorkDirChange?.("");
    setManualPath("");
    setShowMenu(false);
  }, [onWorkDirChange]);

  const loadDirectories = useCallback(async (path: string) => {
    if (!path) {
      setBrowseLoading(true);
      try {
        const res = await fetch(`${getBackendBaseURL()}/api/fs/roots`, {
          headers: authHeaders(),
        });
        const data = await res.json();
        const entries = Array.isArray(data?.entries)
          ? data.entries
          : Array.isArray(data)
            ? data
            : [];
        setBrowseEntries(
          dedupeDirEntries(
            entries.filter((entry: FsTreeEntry) => entry.type === "dir"),
          ),
        );
      } catch (e) {
        swallow(e);
        setBrowseEntries([]);
      } finally {
        setBrowseLoading(false);
      }
      return;
    }
    setBrowseLoading(true);
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/fs/tree?path=${encodeURIComponent(path)}&depth=1`,
        { headers: authHeaders() },
      );
      const data = await res.json();
      const entries = Array.isArray(data?.entries)
        ? data.entries
        : Array.isArray(data)
          ? data
          : [];
      setBrowseEntries(
        dedupeDirEntries(
          entries.filter((entry: FsTreeEntry) => entry.type === "dir"),
        ),
      );
    } catch (e) {
      swallow(e);
      setBrowseEntries([]);
    } finally {
      setBrowseLoading(false);
    }
  }, []);

  // Load remote workspaces from the registry. Cached at component level so
  // the second menu open is instant — we don't refetch on every tab switch.
  const loadRemoteWorkspaces = useCallback(async () => {
    if (!enableRemoteTab || !remoteWorkspaceEnabled) return;
    const canReadRemoteWorkspaces =
      !authLoading && (authStatus?.enabled === false || isAuthenticated);
    if (!canReadRemoteWorkspaces) {
      setRemoteWorkspaces([]);
      setRemoteError(null);
      return;
    }
    setRemoteLoading(true);
    setRemoteError(null);
    try {
      const list = await listWorkspaces();
      setRemoteWorkspaces(list);
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setRemoteError(message);
      swallow(e);
    } finally {
      setRemoteLoading(false);
    }
  }, [
    authLoading,
    authStatus?.enabled,
    enableRemoteTab,
    isAuthenticated,
    remoteWorkspaceEnabled,
  ]);

  // When the menu opens with the remote tab enabled, prime the list so
  // the user isn't staring at an empty state. Subsequent tab switches
  // reuse the cached list.
  useEffect(() => {
    if (!showMenu || !enableRemoteTab || !remoteWorkspaceEnabled) return;
    if (remoteWorkspaces.length > 0 || remoteLoading) return;
    void loadRemoteWorkspaces();
  }, [
    showMenu,
    enableRemoteTab,
    remoteWorkspaceEnabled,
    remoteWorkspaces.length,
    remoteLoading,
    loadRemoteWorkspaces,
  ]);

  const handlePickRemote = useCallback(
    (workspace: Workspace) => {
      onWorkspaceIdChange?.(workspace.id);
      setShowMenu(false);
      setNoBridgeHint(false);
    },
    [onWorkspaceIdChange],
  );

  useEffect(() => {
    if (!workDir) return;
    setManualPath(workDir);
    setBrowsePath(workDir);
    setRecentWorkdirs((prev) => {
      const merged = dedupePaths([workDir, ...prev]).slice(
        0,
        MAX_RECENT_WORKDIRS,
      );
      writeRecentWorkdirs(merged);
      return merged;
    });
  }, [workDir]);

  useEffect(() => {
    if (!showMenu) return;
    if (isMutedVariant && !noBridgeHint) return;
    void loadDirectories(browsePath);
  }, [isMutedVariant, noBridgeHint, showMenu, browsePath, loadDirectories]);

  useEffect(() => {
    if (!showMenu) return;
    const pendingName = pendingBrowserPickedNameRef.current;
    pendingBrowserPickedNameRef.current = "";
    setManualPath(pendingName || workDir);
    if (!workDir && (!isMutedVariant || noBridgeHint)) setBrowserOpen(true);
  }, [isMutedVariant, noBridgeHint, showMenu, workDir]);

  useEffect(() => {
    if (!showMenu) return;
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        !containerRef.current?.contains(target) &&
        !menuRef.current?.contains(target)
      ) {
        setShowMenu(false);
      }
    };
    window.addEventListener("mousedown", handleClickOutside);
    return () => window.removeEventListener("mousedown", handleClickOutside);
  }, [showMenu]);

  const handlePrimaryAction = useCallback(async () => {
    if (isPicking) return;
    setIsPicking(true);
    try {
      const selected = await pickLocalDirectory(workDir);
      if (selected) {
        applyWorkDir(selected);
        return;
      }
    } catch (error) {
      swallow(error);
      setNoBridgeHint(true);
      setBrowserOpen(true);
    } finally {
      setIsPicking(false);
    }

    setShowMenu(true);
    if (!isMutedVariant) setBrowserOpen(true);
    requestAnimationFrame(() => {
      manualInputRef.current?.focus();
    });
  }, [applyWorkDir, isMutedVariant, isPicking, workDir]);

  const handleManualSubmit = useCallback(
    (event: FormEvent) => {
      event.preventDefault();
      applyWorkDir(manualPath);
    },
    [applyWorkDir, manualPath],
  );

  const [menuRect, setMenuRect] = useState<{
    top?: number;
    bottom?: number;
    left: number;
    width: number;
    maxHeight: number;
  } | null>(null);

  const updateMenuPosition = useCallback(() => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const compactMutedMenu = isMutedVariant && !noBridgeHint;
    const targetWidth = compactMutedMenu ? 136 : MENU_WIDTH;
    const minWidth = compactMutedMenu ? 128 : 280;
    const _estimatedHeight = compactMutedMenu ? 56 : 260;
    const minHeight = compactMutedMenu ? 48 : 240;
    const width = Math.min(
      targetWidth,
      Math.max(minWidth, window.innerWidth - MENU_MARGIN * 2),
    );
    const preferredLeft = compactMutedMenu ? rect.left : rect.right - width;
    const left = Math.min(
      Math.max(MENU_MARGIN, preferredLeft),
      window.innerWidth - MENU_MARGIN - width,
    );
    const spaceBelow = window.innerHeight - rect.bottom - MENU_MARGIN;
    const spaceAbove = rect.top - MENU_MARGIN;
    const openUp = spaceAbove > spaceBelow;
    const maxHeight = Math.max(
      minHeight,
      openUp ? spaceAbove - 6 : spaceBelow - 6,
    );
    setMenuRect({
      left,
      width,
      maxHeight,
      ...(openUp
        ? { bottom: window.innerHeight - rect.top + 6 }
        : { top: rect.bottom + 6 }),
    });
  }, [isMutedVariant, noBridgeHint]);

  useEffect(() => {
    if (!showMenu) {
      setMenuRect(null);
      return;
    }
    updateMenuPosition();
    window.addEventListener("resize", updateMenuPosition);
    window.addEventListener("scroll", updateMenuPosition, true);
    return () => {
      window.removeEventListener("resize", updateMenuPosition);
      window.removeEventListener("scroll", updateMenuPosition, true);
    };
  }, [showMenu, updateMenuPosition]);

  const handleEnterDirectory = useCallback(
    (entryPath: string) => {
      setBrowsePath(browseEntryPath(browsePath, entryPath));
    },
    [browsePath],
  );

  const upDir = parentDir(browsePath);

  const handleOpenFolderCta = useCallback(async () => {
    setIsPicking(true);
    try {
      const selected = await pickLocalDirectory(workDir);
      if (selected) {
        applyWorkDir(selected);
        return;
      }
    } catch (error) {
      swallow(error);
      setNoBridgeHint(true);
      setBrowserOpen(true);
    } finally {
      setIsPicking(false);
    }

    setShowMenu(true);
    if (!isMutedVariant) setBrowserOpen(true);
    requestAnimationFrame(() => manualInputRef.current?.focus());
  }, [applyWorkDir, isMutedVariant, workDir]);

  // CTA tile for the implemented workspace picker entry point.
  const cta = (opts: {
    icon: React.ReactNode;
    label: string;
    onClick: () => void;
    disabled?: boolean;
  }) => (
    <button
      type="button"
      onClick={opts.onClick}
      disabled={opts.disabled}
      className={cn(
        "flex w-full items-center gap-2 transition-colors",
        isMutedVariant
          ? "rounded-md px-2 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted/60 hover:text-foreground"
          : "rounded-lg border border-primary/20 bg-primary/10 px-3 py-2 text-sm font-medium text-primary",
        opts.disabled
          ? "cursor-not-allowed opacity-50"
          : !isMutedVariant && "hover:border-primary/30 hover:bg-primary/15",
      )}
    >
      <span
        className={cn(
          "grid shrink-0 place-items-center rounded-md",
          isMutedVariant ? "size-5" : "size-7 bg-background/80",
        )}
      >
        {opts.icon}
      </span>
      <span className="min-w-0 flex-1 text-left">{opts.label}</span>
    </button>
  );

  const folderPickerCta = cta({
    icon: isPicking ? (
      <Loader2Icon
        className={cn("animate-spin", isMutedVariant ? "size-3.5" : "size-4")}
      />
    ) : (
      <FolderOpenIcon className={isMutedVariant ? "size-3.5" : "size-4"} />
    ),
    label: folderPickerLabel,
    onClick: handleOpenFolderCta,
    disabled: isPicking,
  });

  const localMenuContent = (
    <div
      className={cn(
        "flex max-h-full flex-col overflow-hidden",
        // When wrapped in Tabs we drop the outer chrome — Tabs adds its own
        // border/rounded corners. When standalone we keep the original frame.
        remoteWorkspaceEnabled
          ? ""
          : "border border-border-default bg-popover/95 backdrop-blur " +
              (isMutedVariant
                ? "rounded-lg shadow-[var(--shadow-md)]"
                : "rounded-lg shadow-2xl"),
      )}
    >
      {/* The same system picker is available in both the desktop shell and the
          local web app; the backend supplies the absolute path in web mode. */}
      <div className={cn("shrink-0", isMutedVariant ? "p-1.5" : "p-2.5")}>
        {folderPickerCta}
        {isWorkDirLocked && (
          <div className="mt-1.5 rounded-md border border-primary/15 bg-primary/5 px-2 py-1.5 text-xs leading-snug text-muted-foreground">
            {lockedCopy.hint}
          </div>
        )}
        {workDir && !isWorkDirLocked && (
          <button
            type="button"
            onClick={clearWorkDir}
            className={cn(
              "mt-1.5 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs font-medium text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground",
              !isMutedVariant && "border border-border-default",
            )}
          >
            <FolderIcon className="size-3.5 shrink-0" />
            <span className="min-w-0 flex-1 truncate">
              {t.codeMode.personalSpace}
            </span>
          </button>
        )}

        {noBridgeHint && (
          <div className="mt-2 rounded-md border border-border-default bg-muted/40 px-2 py-1.5 text-xs leading-snug text-muted-foreground">
            {webPickerHint(locale)}
          </div>
        )}
        {(!isMutedVariant || noBridgeHint) && (
          <form
            className="mt-2 flex items-center gap-1.5 rounded-lg border border-border-default bg-background/70 p-1 shadow-inner"
            onSubmit={handleManualSubmit}
          >
            <input
              ref={manualInputRef}
              aria-label={t.codeMode.selectWorkspace}
              value={manualPath}
              onChange={(event) => setManualPath(event.target.value)}
              placeholder={t.codeMode.selectWorkspace}
              className="min-w-0 flex-1 bg-transparent px-2 py-1.5 font-mono text-xs text-foreground outline-none placeholder:text-muted-foreground/60"
            />
            <button
              type="submit"
              disabled={!isAbsolutePath(manualPath)}
              className={cn(
                "rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors",
                isAbsolutePath(manualPath)
                  ? "bg-primary text-primary-foreground hover:bg-primary/90"
                  : "cursor-not-allowed bg-muted text-muted-foreground/45",
              )}
            >
              {t.common.confirm}
            </button>
          </form>
        )}
      </div>

      {/* Recent workspaces — colored letter-tile + path subtitle + ✓ for the
          active one. Shown in the muted (web) menu too so there's a one-click
          way to rebind without a native picker. */}
      {(!isMutedVariant || recentWorkdirs.length > 0) && (
        <div
          className={cn(
            "border-t border-border-default",
            isMutedVariant ? "px-1.5 py-1.5" : "px-2.5 py-2",
          )}
        >
          <div className="pb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t.codeMode.recentWorkspaces}
          </div>
          {recentWorkdirs.length > 0 ? (
            <div
              className={cn(
                "space-y-0.5 overflow-y-auto pr-1",
                isMutedVariant ? "max-h-32" : "max-h-44",
              )}
            >
              {recentWorkdirs.map((dir) => {
                const tile = avatarTile(dir);
                const active = dir === workDir;
                return (
                  <button
                    key={dir}
                    type="button"
                    onClick={() => applyWorkDir(dir)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-lg px-1.5 py-1.5 text-left transition-colors",
                      active
                        ? "bg-primary/10 text-primary"
                        : "hover:bg-muted/55",
                    )}
                    title={dir}
                  >
                    <span
                      className={cn(
                        "flex size-7 shrink-0 items-center justify-center rounded-md font-mono text-xs font-semibold",
                        tile.bg,
                        tile.fg,
                      )}
                    >
                      {tile.letter}
                    </span>
                    <span className="min-w-0 flex-1">
                      <div className="truncate text-xs font-medium text-foreground">
                        {basename(dir) || dir}
                      </div>
                      <div className="truncate font-mono text-xs text-muted-foreground/80">
                        {dir}
                      </div>
                    </span>
                    {active && (
                      <CheckIcon className="size-3.5 shrink-0 text-primary" />
                    )}
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-border-default bg-muted/20 px-2 py-3 text-center text-xs text-muted-foreground">
              {t.codeMode.noRecentWorkspaces}
            </div>
          )}
        </div>
      )}

      {/* Advanced: in-page directory browser. Web-mode users without an
          Electron picker still need a way to navigate; native users will
          rarely open this. Collapsed by default so it doesn't dominate. */}
      {(!isMutedVariant || noBridgeHint) && (
        <details
          className="group border-t border-border-default px-2.5 py-2"
          open={isBrowserOpen}
          onToggle={(event) => setBrowserOpen(event.currentTarget.open)}
        >
          <summary className="cursor-pointer list-none rounded-md px-1 py-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground">
            <span className="inline-flex items-center gap-1">
              <ChevronDownIcon className="size-3 transition-transform group-open:rotate-180" />
              {t.codeMode.browseCurrentFolder}
            </span>
          </summary>
          <div className="mt-1">
            <div
              className="px-1 pb-1 text-xs font-mono text-muted-foreground/80 truncate"
              title={browsePath}
            >
              {browsePath || "—"}
            </div>
            {upDir && (
              <button
                type="button"
                onClick={() => setBrowsePath(upDir)}
                className="block w-full rounded-md px-2 py-1.5 text-left text-xs text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
              >
                .. / {t.codeMode.parentFolder}
              </button>
            )}
            {browseLoading ? (
              <div className="flex items-center gap-2 px-2 py-2 text-xs text-muted-foreground">
                <Loader2Icon className="size-3 animate-spin" />
                {t.codeMode.loadingFolders}
              </div>
            ) : browseEntries.length > 0 ? (
              <div
                className={cn(
                  "mt-1 space-y-0.5 overflow-auto",
                  isMutedVariant ? "max-h-28" : "max-h-56",
                )}
              >
                {browseEntries.map((entry) => {
                  const nextPath = browseEntryPath(browsePath, entry.path);
                  const displayName = entryDisplayName(entry);
                  return (
                    <div
                      key={`${browsePath}:${entry.path}`}
                      className="flex items-center gap-1"
                    >
                      <button
                        type="button"
                        onClick={() => handleEnterDirectory(entry.path)}
                        className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
                        title={nextPath}
                      >
                        <FolderIcon className="size-3 shrink-0" />
                        <span className="truncate">{displayName}</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => applyWorkDir(nextPath)}
                        className="rounded-md px-2 py-1 text-xs text-primary transition-colors hover:bg-primary/10"
                        title={t.codeMode.chooseWorkspaceFolder}
                      >
                        ✓
                      </button>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="px-2 py-2 text-xs text-muted-foreground">
                {t.codeMode.noSubfolders}
              </div>
            )}
          </div>
        </details>
      )}
    </div>
  );

  const remoteMenuContent = (
    <div
      className={cn(
        "flex max-h-full flex-col overflow-hidden",
        remoteWorkspaceEnabled
          ? ""
          : "border border-border-default bg-popover/95 backdrop-blur rounded-lg shadow-2xl",
      )}
    >
      <div className={cn("shrink-0", isMutedVariant ? "p-1.5" : "p-2.5")}>
        <div className="mb-2 flex items-center justify-between gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {trRemote.switcherTitle}
          </span>
          <button
            type="button"
            onClick={() => void loadRemoteWorkspaces()}
            disabled={remoteLoading}
            className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground disabled:opacity-50"
            title={trRemote.searchPlaceholder}
            aria-label={trRemote.searchPlaceholder}
          >
            <Loader2Icon
              className={cn("size-3", remoteLoading && "animate-spin")}
            />
          </button>
        </div>

        {remoteError && (
          <div className="mb-2 rounded-md border border-destructive/30 bg-destructive/5 px-2 py-1.5 text-xs text-destructive">
            {trRemote.remoteLoadFailed(remoteError)}
          </div>
        )}

        {remoteLoading && remoteWorkspaces.length === 0 ? (
          <div className="flex items-center gap-2 px-2 py-3 text-xs text-muted-foreground">
            <Loader2Icon className="size-3 animate-spin" />
            {trRemote.remoteLoading}
          </div>
        ) : remoteWorkspaces.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border-default bg-muted/20 px-2 py-3 text-center text-xs text-muted-foreground">
            {trRemote.remoteEmpty}
          </div>
        ) : (
          <div className="space-y-0.5 overflow-y-auto pr-1 max-h-72">
            {remoteWorkspaces.map((ws) => {
              const Icon = MOUNT_TYPE_ICON[ws.mount_type];
              const active = ws.id === workspaceId;
              return (
                <button
                  key={ws.id}
                  type="button"
                  onClick={() => handlePickRemote(ws)}
                  title={ws.mount_target}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-lg px-1.5 py-1.5 text-left transition-colors",
                    active ? "bg-primary/10 text-primary" : "hover:bg-muted/55",
                  )}
                >
                  <span
                    className={cn(
                      "flex size-7 shrink-0 items-center justify-center rounded-md",
                      active
                        ? "bg-primary/15 text-primary"
                        : "bg-muted/40 text-muted-foreground",
                    )}
                  >
                    <Icon className="size-3.5" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium text-foreground">
                      {ws.name}
                    </span>
                    <span className="block truncate font-mono text-xs text-muted-foreground/80">
                      {ws.mount_target}
                    </span>
                  </span>
                  {active && (
                    <CheckIcon className="size-3.5 shrink-0 text-primary" />
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );

  // When the remote tab is enabled we wrap the two panels in a shadcn Tabs
  // so the user can flip between local-folder and remote-mount entry
  // points. Otherwise we render the local panel as before — no visual
  // change for existing callers.
  const menuContent = remoteWorkspaceEnabled ? (
    <div
      className={cn(
        "flex max-h-full flex-col overflow-hidden rounded-lg border border-border-default bg-popover/95 backdrop-blur",
        isMutedVariant ? "shadow-[var(--shadow-md)]" : "shadow-2xl",
      )}
    >
      <Tabs
        value={menuTab}
        onValueChange={(value) => setMenuTab(value as "local" | "remote")}
        className="flex min-h-0 flex-1 flex-col"
      >
        <TabsList className="mx-2 mt-2 grid h-8 shrink-0 grid-cols-2 rounded-md bg-muted/50">
          <TabsTrigger value="local" className="text-xs">
            {trRemote.localTab}
          </TabsTrigger>
          <TabsTrigger value="remote" className="text-xs">
            {trRemote.remoteTab}
          </TabsTrigger>
        </TabsList>
        <TabsContent
          value="local"
          className="mt-0 min-h-0 flex-1 overflow-y-auto"
        >
          {localMenuContent}
        </TabsContent>
        <TabsContent
          value="remote"
          className="mt-0 min-h-0 flex-1 overflow-y-auto"
        >
          {remoteMenuContent}
        </TabsContent>
      </Tabs>
    </div>
  ) : (
    localMenuContent
  );

  return (
    <div
      ref={containerRef}
      className={cn("relative flex items-center gap-1.5", className)}
    >
      <button
        className={cn(
          "group flex items-center gap-1.5 text-xs font-medium shadow-none transition-colors duration-base",
          chromeless
            ? "h-8 rounded-lg px-1.5 text-muted-foreground hover:bg-muted/55 hover:text-foreground"
            : "h-8 rounded-lg border border-transparent bg-transparent px-2 text-muted-foreground hover:border-border-default hover:bg-muted/55 hover:text-foreground",
          isEmpty ? emptyTriggerClass : activeTriggerClass,
          isPicking && "cursor-wait opacity-50",
        )}
        onClick={handlePrimaryAction}
        disabled={isPicking}
        title={triggerTitle}
        type="button"
      >
        <FolderOpenIcon className="size-3 shrink-0 opacity-70" />
        <span
          className={cn(
            isMutedVariant
              ? "max-w-[120px] truncate"
              : "max-w-[160px] truncate",
            isEmpty ? "font-medium" : "tracking-normal",
          )}
        >
          {triggerLabel}
        </span>
        <ChevronDownIcon className="size-3 shrink-0 opacity-35 transition-opacity group-hover:opacity-60" />
      </button>

      {menuRect && typeof document !== "undefined"
        ? createPortal(
            <div
              ref={menuRef}
              className="fixed z-[100]"
              style={{
                top:
                  menuRect.top !== undefined ? `${menuRect.top}px` : undefined,
                bottom:
                  menuRect.bottom !== undefined
                    ? `${menuRect.bottom}px`
                    : undefined,
                left: `${menuRect.left}px`,
                width: `${menuRect.width}px`,
                maxHeight: `${menuRect.maxHeight}px`,
              }}
              onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => e.stopPropagation()}
            >
              {menuContent}
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
