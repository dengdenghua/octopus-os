import { useCallback, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import {
  BotIcon,
  BrainIcon,
  DatabaseIcon,
  DnaIcon,
  CompassIcon,
  MessageSquareIcon,
  MessageSquarePlusIcon,
  MoreHorizontalIcon,
  PencilIcon,
  SearchIcon,
  SettingsIcon,
  UserRoundPenIcon,
  Trash2Icon,
} from "lucide-react";

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { useActiveAgentId } from "@/core/agents/active";
import { emitAgentChanged, emitOpenSettings, eventBus } from "@/core/events";
import { useI18n } from "@/core/i18n/hooks";
import {
  useDeleteThread,
  useRenameThread,
  useThreads,
} from "@/core/threads/hooks";
import type { AgentThread } from "@/core/threads/types";
import { formatCompactRelativeTimestamp } from "@/core/utils/datetime";
import { uuid } from "@/core/utils/uuid";
import { activeWorkspaceThreadIdFromPathname } from "@/core/threads/sidebar";
import { isIMEComposing } from "@/lib/ime";
import { isAbsolutePath } from "@/lib/path-utils";
import { cn } from "@/lib/utils";

const DRAWER_WIDTH = "min(320px, 86vw)";

/* Implementation note. */
function deriveTitle(thread: AgentThread): string {
  const meta = (thread.metadata ?? {}) as Record<string, unknown>;
  const metaTitle =
    typeof meta["title"] === "string" ? (meta["title"] as string).trim() : "";
  if (metaTitle) {
    return metaTitle.length > 60 ? `${metaTitle.slice(0, 58)}...` : metaTitle;
  }
  const values = (thread.values ?? {}) as Record<string, unknown>;
  const valuesTitle =
    typeof values["title"] === "string"
      ? (values["title"] as string).trim()
      : "";
  if (valuesTitle && valuesTitle !== "New chat" && valuesTitle !== "New task") {
    return valuesTitle.length > 60
      ? `${valuesTitle.slice(0, 58)}...`
      : valuesTitle;
  }
  const messages = values["messages"];
  if (Array.isArray(messages)) {
    for (const m of messages) {
      if (
        m &&
        typeof m === "object" &&
        (m as Record<string, unknown>).type === "human"
      ) {
        const content = (m as Record<string, unknown>).content;
        const text =
          typeof content === "string"
            ? content.replace(/\s+/g, " ").trim()
            : "";
        if (text) {
          return text.length > 60 ? `${text.slice(0, 58)}...` : text;
        }
      }
    }
  }
  return `对话/${thread.thread_id.slice(0, 6)}`;
}

function threadHref(thread: AgentThread): string {
  return `/workspace/realtime/${encodeURIComponent(thread.thread_id)}`;
}

function threadOwnerAgent(thread: AgentThread): string {
  const meta = (thread.metadata ?? {}) as Record<string, unknown>;
  const values = (thread.values ?? {}) as Record<string, unknown>;
  const candidates = [
    meta["agent"],
    meta["agent_name"],
    meta["agent_id"],
    meta["lead_agent_name"],
    meta["current_agent"],
    values["current_speaker"],
    values["agent_name"],
  ];
  for (const value of candidates) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function threadWorkspacePath(thread?: AgentThread): string {
  const meta = (thread?.metadata ?? {}) as Record<string, unknown>;
  const values = (thread?.values ?? {}) as Record<string, unknown>;
  const path = meta["workspace_path"] ?? values["workspace_path"];
  return typeof path === "string" ? path.trim() : "";
}

interface ChatsDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ChatsDrawer({ open, onOpenChange }: ChatsDrawerProps) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const deleteThread = useDeleteThread();
  const { mutate: renameThread } = useRenameThread();
  const [query, setQuery] = useState("");
  const { confirm, confirmDialog } = useConfirmDialog();
  const [threadToRename, setThreadToRename] = useState<AgentThread | null>(
    null,
  );
  const [renameValue, setRenameValue] = useState("");
  const activeAgentId = useActiveAgentId();
  const workspaceDestinations = [
    {
      label: t.sidebar.navHR,
      to: "/workspace/agents?surface=chat",
      icon: BotIcon,
      active: pathname.startsWith("/workspace/agents"),
    },
    {
      label: t.sidebar.navIntelligence,
      to: "/workspace/intelligence?surface=chat",
      icon: BrainIcon,
      active: pathname.startsWith("/workspace/intelligence"),
    },
    {
      label: t.sidebar.navAssistant,
      to: "/workspace/realtime/echo-assistant?agent=echo",
      icon: UserRoundPenIcon,
      active: pathname.includes("echo-assistant"),
    },
    {
      label: t.sidebar.navEvolution,
      to: "/workspace/evolution?surface=chat",
      icon: DnaIcon,
      active: pathname.startsWith("/workspace/evolution"),
    },
    {
      label: t.sidebar.navCommunity,
      to: "/workspace/community",
      icon: CompassIcon,
      active: pathname.startsWith("/workspace/community"),
    },
    {
      label: t.sidebar.navDatabase,
      to: "/workspace/storage?surface=company&library=docs",
      icon: DatabaseIcon,
      active: pathname.startsWith("/workspace/storage"),
    },
  ];

  const handleRenameSubmit = useCallback(() => {
    if (threadToRename && renameValue.trim()) {
      renameThread({
        threadId: threadToRename.thread_id,
        title: renameValue.trim(),
      });
      setThreadToRename(null);
      setRenameValue("");
    }
  }, [renameThread, threadToRename, renameValue]);

  // White Ghost members keep their own lanes. Historical expert/CLI/device
  // tasks are shared because those actors now join conversations on demand.
  const { data: threads = [] } = useThreads(
    {
      limit: 50,
      sortBy: "updated_at",
      sortOrder: "desc",
      select: ["thread_id", "updated_at", "values", "metadata"],
    },
    undefined,
    activeAgentId,
  );

  const filteredThreads = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return threads;
    return threads.filter((t) => deriveTitle(t).toLowerCase().includes(q));
  }, [threads, query]);

  const startNewChat = useCallback(() => {
    onOpenChange(false);
    const activeId = activeWorkspaceThreadIdFromPathname(pathname);
    const activeThread = activeId
      ? threads.find((thread) => thread.thread_id === activeId)
      : undefined;
    const routeWorkspacePath =
      new URLSearchParams(search).get("workspace_path") ?? "";
    const threadPath = threadWorkspacePath(activeThread);
    const workspacePath = isAbsolutePath(threadPath)
      ? threadPath
      : isAbsolutePath(routeWorkspacePath)
        ? routeWorkspacePath
        : undefined;
    eventBus.emit("task:new", {
      agentId: activeThread
        ? threadOwnerAgent(activeThread) || undefined
        : undefined,
      workspacePath,
    });
  }, [onOpenChange, pathname, search, threads]);

  const openSettings = useCallback(() => {
    onOpenChange(false);
    // Let the sheet release its modal focus/aria guards before opening the
    // settings dialog. Opening both in the same event turn causes Radix to
    // immediately dismiss the second surface on narrow screens.
    window.setTimeout(() => {
      emitOpenSettings();
    }, 0);
  }, [onOpenChange]);

  const handleDelete = useCallback(
    async (thread: AgentThread) => {
      const ok = await confirm({
        title: t.sidebar.deleteThreadTooltip,
        description: t.sidebar.confirmDeleteThread(deriveTitle(thread)),
      });
      if (!ok) return;
      deleteThread.mutate({ threadId: thread.thread_id });
      if (pathname === threadHref(thread)) {
        navigate(`/workspace/realtime/${uuid()}`, { replace: true });
      }
    },
    [confirm, deleteThread, navigate, pathname, t],
  );

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent
          side="left"
          className={cn(
            "gap-0 p-0",
            "data-[state=open]:duration-slow data-[state=closed]:duration-base",
          )}
          style={{ width: DRAWER_WIDTH, maxWidth: DRAWER_WIDTH }}
        >
          <SheetHeader className="border-b border-border-subtle px-4 py-3 pr-12">
            <SheetTitle className="text-sm font-semibold">
              {t.sidebar.sectionChats}
            </SheetTitle>
            <SheetDescription className="sr-only">
              {t.sidebar.sectionChats}
            </SheetDescription>
          </SheetHeader>

          <nav
            aria-label={t.sidebar.navigate}
            className="border-b border-border-subtle px-3 py-3"
          >
            <div className="mb-1.5 px-1 text-xs font-medium uppercase tracking-wider text-muted-foreground/70">
              {t.sidebar.navigate}
            </div>
            <div className="grid grid-cols-2 gap-1.5">
              {workspaceDestinations.map((item) => {
                const Icon = item.icon;
                return (
                  <Link
                    key={item.to}
                    to={item.to}
                    onClick={() => onOpenChange(false)}
                    aria-current={item.active ? "page" : undefined}
                    className={cn(
                      "flex h-10 min-w-0 items-center gap-2 rounded-md px-2.5 text-caption font-medium outline-none transition-colors",
                      "focus-visible:ring-2 focus-visible:ring-primary/55 focus-visible:ring-offset-1",
                      item.active
                        ? "bg-muted/70 text-foreground"
                        : "text-muted-foreground hover:bg-muted/55 hover:text-foreground",
                    )}
                  >
                    <Icon className="size-3.5 shrink-0" />
                    <span className="truncate">{item.label}</span>
                  </Link>
                );
              })}
            </div>
          </nav>

          <div className="flex flex-col gap-2 px-3 pt-3">
            <button
              type="button"
              onClick={startNewChat}
              className={cn(
                "group flex h-10 w-full items-center justify-center gap-2 outline-none",
                "border border-primary/30 bg-primary/8 text-sm font-medium text-primary",
                "transition-colors hover:bg-primary/14 active:scale-[0.99]",
                "focus-visible:ring-2 focus-visible:ring-primary/55 focus-visible:ring-offset-1",
              )}
            >
              <MessageSquarePlusIcon className="size-4" />
              {t.sidebar.actionNewTask}
            </button>

            <div className="relative">
              <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/70" />
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t.sidebar.searchChats}
                className="h-10 pl-8 text-caption"
              />
            </div>
          </div>

          <div className="mt-3 flex items-center justify-between px-4 text-xs font-medium uppercase tracking-wider text-muted-foreground/70">
            <span className="flex items-center gap-1.5">
              <MessageSquareIcon className="size-3" />
              {t.sidebar.recentChats}
            </span>
            <span className="text-xs text-muted-foreground/55">
              {filteredThreads.length}
            </span>
          </div>

          <div className="mt-1 min-h-0 flex-1 overflow-y-auto px-2 pb-3">
            {filteredThreads.length === 0 ? (
              <div className="mt-4 rounded-md border border-dashed border-border-default px-3 py-4 text-center text-xs text-muted-foreground/75">
                {query.trim()
                  ? t.sidebar.noMatchingChats
                  : t.sidebar.noChatsYet}
              </div>
            ) : (
              <ul className="flex flex-col gap-px">
                {filteredThreads.map((thread) => {
                  const href = threadHref(thread);
                  const active =
                    activeWorkspaceThreadIdFromPathname(pathname) ===
                    thread.thread_id;
                  return (
                    <li
                      key={thread.thread_id}
                      className="group/thread relative"
                    >
                      <Link
                        to={href}
                        state={{
                          threadOwnerAgentId:
                            threadOwnerAgent(thread) || undefined,
                          workspacePath:
                            threadWorkspacePath(thread) || undefined,
                        }}
                        onMouseDown={() => {
                          const owner = threadOwnerAgent(thread);
                          if (owner) emitAgentChanged(owner, "thread");
                        }}
                        onClick={() => onOpenChange(false)}
                        aria-current={active ? "page" : undefined}
                        className={cn(
                          "flex min-h-10 items-center gap-2 rounded-md px-2 py-1.5 text-caption outline-none transition-colors",
                          "hover:bg-muted/55",
                          "focus-visible:ring-2 focus-visible:ring-primary/45 focus-visible:ring-inset",
                          active &&
                            "bg-[color:color-mix(in_oklch,var(--sidebar-accent)_55%,transparent)] font-medium",
                        )}
                      >
                        <span className="min-w-0 flex-1 truncate leading-tight">
                          {deriveTitle(thread)}
                        </span>
                        <span
                          className={cn(
                            "w-10 shrink-0 overflow-hidden whitespace-nowrap text-right text-xs text-muted-foreground/65 [@media(hover:none)]:mr-6",
                            "transition-[width,opacity] group-hover/thread:w-0 group-hover/thread:opacity-0",
                          )}
                        >
                          {formatCompactRelativeTimestamp(thread.updated_at)}
                        </span>
                      </Link>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <button
                            type="button"
                            title={t.common.more}
                            aria-label={t.common.more}
                            onClick={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                            }}
                            className={cn(
                              "absolute right-1 top-1/2 -translate-y-1/2 flex size-7 items-center justify-center rounded-md outline-none",
                              "text-muted-foreground/60 opacity-0 transition-opacity [@media(hover:none)]:opacity-100",
                              "hover:bg-muted/60 hover:text-foreground",
                              "focus-visible:bg-muted/60 focus-visible:text-foreground focus-visible:opacity-100",
                              "group-hover/thread:opacity-100 data-[state=open]:opacity-100",
                            )}
                          >
                            <MoreHorizontalIcon className="size-3.5" />
                          </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" side="right">
                          <DropdownMenuItem
                            onSelect={() => {
                              setThreadToRename(thread);
                              setRenameValue(deriveTitle(thread));
                            }}
                          >
                            <PencilIcon className="text-muted-foreground" />
                            <span>{t.common.rename}</span>
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            variant="destructive"
                            disabled={deleteThread.isPending}
                            onSelect={() => void handleDelete(thread)}
                          >
                            <Trash2Icon />
                            <span>{t.sidebar.deleteThreadTooltip}</span>
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <div className="border-t border-border-subtle p-2">
            <button
              type="button"
              onClick={openSettings}
              className="flex h-10 w-full items-center gap-2 rounded-md px-2.5 text-caption font-medium text-muted-foreground outline-none transition-colors hover:bg-muted/60 hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary/45 focus-visible:ring-inset"
            >
              <SettingsIcon className="size-4" />
              {t.common.settings}
            </button>
          </div>
        </SheetContent>
      </Sheet>
      <Dialog
        open={threadToRename !== null}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) {
            setThreadToRename(null);
            setRenameValue("");
          }
        }}
      >
        <DialogContent
          showCloseButton={false}
          className="w-[min(360px,calc(100vw-2rem))] gap-3 rounded-lg p-4 sm:max-w-[360px]"
        >
          <DialogHeader className="gap-1 text-left">
            <DialogTitle className="text-base">{t.common.rename}</DialogTitle>
          </DialogHeader>
          <Input
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !isIMEComposing(e)) {
                e.preventDefault();
                handleRenameSubmit();
              }
            }}
            autoFocus
            className="h-8 text-sm"
          />
          <DialogFooter className="mt-1 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                setThreadToRename(null);
                setRenameValue("");
              }}
            >
              {t.common.cancel}
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={!renameValue.trim()}
              onClick={handleRenameSubmit}
            >
              {t.common.save}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {confirmDialog}
    </>
  );
}
