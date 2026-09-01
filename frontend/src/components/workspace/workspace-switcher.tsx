/**
 * Workspace Switcher · pick the active remote/local workspace for the
 * current thread.
 *
 * Renders a compact trigger at the top of the sidebar. Clicking opens a
 * popover-style panel with:
 *   - search filter (by name or mount target)
 *   - per-workspace row: type icon + name + mount target subtitle
 *   - "Add workspace" button → opens MountPointDialog
 *
 * Switching calls ``onSwitch`` with the picked workspace. The parent
 * owns the persistence step (updating the thread's ``workspace_id`` via
 * ``POST /threads/{id}/state``) — same split as WorkDirSelector.
 */

import {
  CheckIcon,
  ChevronDownIcon,
  CloudIcon,
  DatabaseIcon,
  HardDriveIcon,
  Loader2Icon,
  PlusIcon,
  SearchIcon,
  ServerIcon,
  TerminalIcon,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useI18n } from "@/core/i18n/hooks";
import { listWorkspaces } from "@/core/workspace/api";
import { useAuth } from "@/providers/AuthProvider";
import { useFeatureFlags } from "@/hooks/use-feature-flags";
import type { MountType, Workspace } from "@/core/workspace/types";
import { MountPointDialog } from "./mount-point-dialog";
import { swallow } from "@/core/utils/log";
import { cn } from "@/lib/utils";

interface WorkspaceSwitcherProps {
  activeWorkspaceId?: string | null;
  onSwitch?: (workspace: Workspace) => void;
  className?: string;
}

const MOUNT_TYPE_ICON: Record<MountType, LucideIcon> = {
  local: HardDriveIcon,
  smb: ServerIcon,
  nfs: ServerIcon,
  webdav: CloudIcon,
  sftp: TerminalIcon,
  s3: DatabaseIcon,
};

const MOUNT_TYPE_LABEL_KEY: Record<
  MountType,
  "typeLocal" | "typeSmb" | "typeNfs" | "typeWebdav" | "typeSftp" | "typeS3"
> = {
  local: "typeLocal",
  smb: "typeSmb",
  nfs: "typeNfs",
  webdav: "typeWebdav",
  sftp: "typeSftp",
  s3: "typeS3",
};

const MENU_WIDTH = 320;
const MENU_MARGIN = 12;

/** Keep the shared sidebar quiet until there is an actual choice to make. */
export function shouldShowWorkspaceSwitcher(workspaces: Workspace[]): boolean {
  return workspaces.length > 1;
}

export function WorkspaceSwitcher({
  activeWorkspaceId,
  onSwitch,
  className,
}: WorkspaceSwitcherProps) {
  const { t } = useI18n();
  const { authStatus, isAuthenticated, isLoading: authLoading } = useAuth();
  const { loading: featureFlagsLoading, isOn: isFeatureOn } = useFeatureFlags();
  const tr = t.remoteWorkspace;
  const containerRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [query, setQuery] = useState("");
  const [mountDialogOpen, setMountDialogOpen] = useState(false);
  const [menuRect, setMenuRect] = useState<{
    top?: number;
    bottom?: number;
    left: number;
    width: number;
    maxHeight: number;
  } | null>(null);

  const activeWorkspace = useMemo(
    () => workspaces.find((ws) => ws.id === activeWorkspaceId) ?? null,
    [workspaces, activeWorkspaceId],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return workspaces;
    return workspaces.filter((ws) => {
      return (
        ws.name.toLowerCase().includes(q) ||
        ws.mount_target.toLowerCase().includes(q) ||
        ws.mount_type.toLowerCase().includes(q)
      );
    });
  }, [workspaces, query]);

  const reload = useCallback(async () => {
    const canReadRemoteWorkspaces =
      !authLoading &&
      !featureFlagsLoading &&
      isFeatureOn("ui.remote_workspace") &&
      (authStatus?.enabled === false || isAuthenticated);
    if (!canReadRemoteWorkspaces) {
      setWorkspaces([]);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const list = await listWorkspaces();
      setWorkspaces(list);
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setError(message);
      swallow(e);
    } finally {
      setLoading(false);
    }
  }, [
    authLoading,
    authStatus?.enabled,
    featureFlagsLoading,
    isAuthenticated,
    isFeatureOn,
  ]);

  useEffect(() => {
    // Load once on mount so the switcher can disappear entirely in the common
    // one-workspace case, rather than reserving a permanent navigation row.
    void reload();
  }, [reload]);

  useEffect(() => {
    const refresh = () => void reload();
    window.addEventListener("echo:workspaces-changed", refresh);
    return () =>
      window.removeEventListener("echo:workspaces-changed", refresh);
  }, [reload]);

  const updateMenuPosition = useCallback(() => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const width = Math.min(
      MENU_WIDTH,
      Math.max(240, window.innerWidth - MENU_MARGIN * 2),
    );
    const left = Math.min(
      Math.max(MENU_MARGIN, rect.left),
      window.innerWidth - MENU_MARGIN - width,
    );
    const spaceBelow = window.innerHeight - rect.bottom - MENU_MARGIN;
    const spaceAbove = rect.top - MENU_MARGIN;
    const openUp = spaceAbove > spaceBelow;
    const maxHeight = Math.max(200, openUp ? spaceAbove - 6 : spaceBelow - 6);
    setMenuRect({
      left,
      width,
      maxHeight,
      ...(openUp
        ? { bottom: window.innerHeight - rect.top + 6 }
        : { top: rect.bottom + 6 }),
    });
  }, []);

  useEffect(() => {
    if (!open) {
      setMenuRect(null);
      setQuery("");
      return;
    }
    updateMenuPosition();
    window.addEventListener("resize", updateMenuPosition);
    window.addEventListener("scroll", updateMenuPosition, true);
    return () => {
      window.removeEventListener("resize", updateMenuPosition);
      window.removeEventListener("scroll", updateMenuPosition, true);
    };
  }, [open, updateMenuPosition]);

  useEffect(() => {
    if (!open) return;
    const handler = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        !containerRef.current?.contains(target) &&
        !menuRef.current?.contains(target)
      ) {
        setOpen(false);
      }
    };
    window.addEventListener("mousedown", handler);
    return () => window.removeEventListener("mousedown", handler);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    requestAnimationFrame(() => searchInputRef.current?.focus());
  }, [open]);

  const handlePick = useCallback(
    (workspace: Workspace) => {
      onSwitch?.(workspace);
      setOpen(false);
    },
    [onSwitch],
  );

  const handleCreated = useCallback(
    (workspace: Workspace) => {
      setWorkspaces((prev) =>
        prev.some((ws) => ws.id === workspace.id) ? prev : [workspace, ...prev],
      );
      handlePick(workspace);
    },
    [handlePick],
  );

  const ActiveIcon = activeWorkspace
    ? MOUNT_TYPE_ICON[activeWorkspace.mount_type]
    : HardDriveIcon;

  // A switcher with no alternative is visual noise, especially on library
  // pages where it reads like a second level of navigation. The selected
  // workspace remains attached to the thread; only this redundant trigger is
  // hidden. If loading fails we fail closed as well, so an unavailable remote
  // workspace API never leaves a dead control in the sidebar.
  if (!shouldShowWorkspaceSwitcher(workspaces)) {
    return null;
  }

  return (
    <div
      ref={containerRef}
      className={cn("relative flex items-center", className)}
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-label={tr.switcherAria}
        aria-haspopup="menu"
        aria-expanded={open}
        title={
          activeWorkspace
            ? `${activeWorkspace.name} · ${activeWorkspace.mount_target}`
            : tr.switcherTitle
        }
        className={cn(
          "group flex h-8 w-full items-center gap-2 rounded-lg border border-transparent bg-transparent px-2 text-xs font-medium text-muted-foreground shadow-none transition-colors",
          "hover:border-border-default hover:bg-muted/55 hover:text-foreground",
          open && "border-border-default bg-muted/55 text-foreground",
          "group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0",
        )}
      >
        <span
          className={cn(
            "flex size-5 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors group-hover:text-foreground",
            open && "text-foreground",
          )}
        >
          <ActiveIcon className="size-3.5" />
        </span>
        <span
          className={cn(
            "min-w-0 flex-1 truncate text-left",
            "group-data-[collapsible=icon]:hidden",
          )}
        >
          {activeWorkspace?.name || tr.switcherTitle}
        </span>
        <ChevronDownIcon
          className={cn(
            "size-3 shrink-0 opacity-50 transition-transform group-hover:opacity-75",
            open && "rotate-180 opacity-80",
            "group-data-[collapsible=icon]:hidden",
          )}
        />
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
              onMouseDown={(event) => event.stopPropagation()}
              onClick={(event) => event.stopPropagation()}
            >
              <div className="flex max-h-full flex-col overflow-hidden rounded-lg border border-border-default bg-popover/95 shadow-[var(--shadow-floating)] backdrop-blur">
                <div className="shrink-0 border-b border-border-default p-2">
                  <div className="flex items-center gap-1.5 rounded-md border border-border-default bg-background/70 px-2 py-1">
                    <SearchIcon className="size-3 shrink-0 text-muted-foreground/70" />
                    <input
                      ref={searchInputRef}
                      type="text"
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      placeholder={tr.searchPlaceholder}
                      aria-label={tr.searchPlaceholder}
                      className="min-w-0 flex-1 bg-transparent text-xs text-foreground outline-none placeholder:text-muted-foreground/60"
                    />
                  </div>
                </div>

                <div
                  className="min-h-0 flex-1 overflow-y-auto p-1"
                  style={{ maxHeight: menuRect.maxHeight - 60 }}
                >
                  {loading && workspaces.length === 0 ? (
                    <div className="flex items-center gap-2 px-2 py-3 text-xs text-muted-foreground">
                      <Loader2Icon className="size-3 animate-spin" />
                      {tr.loading}
                    </div>
                  ) : error ? (
                    <div className="px-2 py-3 text-xs text-destructive">
                      {tr.loadFailed(error)}
                    </div>
                  ) : filtered.length === 0 ? (
                    <div className="px-2 py-3 text-xs text-muted-foreground">
                      {tr.empty}
                    </div>
                  ) : (
                    <ul className="space-y-0.5">
                      {filtered.map((ws) => {
                        const Icon = MOUNT_TYPE_ICON[ws.mount_type];
                        const isActive = ws.id === activeWorkspaceId;
                        const typeLabel =
                          tr[MOUNT_TYPE_LABEL_KEY[ws.mount_type]];
                        return (
                          <li key={ws.id}>
                            <button
                              type="button"
                              onClick={() => handlePick(ws)}
                              aria-label={tr.switchWorkspaceAria(ws.name)}
                              title={ws.mount_target}
                              className={cn(
                                "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors",
                                isActive
                                  ? "bg-primary/10 text-primary"
                                  : "text-foreground hover:bg-muted/60",
                              )}
                            >
                              <span
                                className={cn(
                                  "flex size-6 shrink-0 items-center justify-center rounded-md",
                                  isActive
                                    ? "bg-primary/15 text-primary"
                                    : "bg-muted/40 text-muted-foreground",
                                )}
                              >
                                <Icon className="size-3.5" />
                              </span>
                              <span className="min-w-0 flex-1">
                                <span className="flex items-center gap-1.5">
                                  <span className="truncate text-xs font-medium">
                                    {ws.name}
                                  </span>
                                  {isActive && (
                                    <span className="shrink-0 text-xs uppercase tracking-wide text-primary/80">
                                      {tr.activeWorkspace}
                                    </span>
                                  )}
                                </span>
                                <span className="block truncate font-mono text-xs text-muted-foreground/80">
                                  {typeLabel} · {ws.mount_target}
                                </span>
                              </span>
                              {isActive && (
                                <CheckIcon className="size-3 shrink-0 text-primary" />
                              )}
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>

                <div className="shrink-0 border-t border-border-default p-1.5">
                  <button
                    type="button"
                    onClick={() => {
                      setOpen(false);
                      setMountDialogOpen(true);
                    }}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs font-medium transition-colors",
                      "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                    )}
                  >
                    <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                      <PlusIcon className="size-3.5" />
                    </span>
                    <span className="min-w-0 flex-1">{tr.addWorkspace}</span>
                  </button>
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}

      <MountPointDialog
        open={mountDialogOpen}
        onOpenChange={setMountDialogOpen}
        onCreated={handleCreated}
      />
    </div>
  );
}
