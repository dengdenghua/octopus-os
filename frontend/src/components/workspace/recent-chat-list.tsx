import {
  ChevronDownIcon,
  Download,
  FileJson,
  FileText,
  FolderIcon,
  MoreHorizontal,
  Pencil,
  PlusIcon,
  Share2,
  Trash2,
} from "lucide-react";
import { Link, useParams, useLocation, useNavigate } from "react-router-dom";
import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { getAPIClient } from "@/core/api";
import { useActiveAgentId } from "@/core/agents/active";
import { copyTextToClipboard } from "@/core/clipboard";
import { emitAgentChanged } from "@/core/events";
import { useI18n } from "@/core/i18n/hooks";
import {
  type Project,
  useDeleteProject,
  useEnsureProjectHome,
  useMoveThreadToProject,
  useProjects,
  useThreadMap,
} from "@/core/projects/hooks";
import {
  exportThreadAsJSON,
  exportThreadAsMarkdown,
} from "@/core/threads/export";
import {
  useDeleteThread,
  useRenameThread,
  useThreads,
} from "@/core/threads/hooks";
import type { AgentThread, AgentThreadState } from "@/core/threads/types";
import { titleOfThread } from "@/core/threads/utils";
import { env } from "@/env";
import { isIMEComposing } from "@/lib/ime";

import { CreateProjectDialog } from "./create-project-dialog";

type ThreadGroup = {
  key: string;
  label: string;
  icon?: string;
  project?: Project;
  threads: AgentThread[];
};

function routeForThread(thread: AgentThread) {
  return `/workspace/realtime/${encodeURIComponent(thread.thread_id)}`;
}

function ownerAgentForThread(thread: AgentThread): string {
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

function workspacePathForThread(thread: AgentThread): string {
  const path = thread.metadata?.workspace_path;
  return typeof path === "string" ? path.trim() : "";
}

export function RecentChatList() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const params = useParams();
  const threadIdFromPath = params.threadId ?? params.thread_id;
  const currentMode = pathname.startsWith("/workspace/realtime")
    ? ("code" as const)
    : undefined; // non-task chat surfaces show this agent's chat/react/deep history

  // Scope personal chat/code history by active agent. Without this, switching
  // a historical conversation can show the wrong persona avatar/header while
  // the list still contains another agent.
  const activeAgentId = useActiveAgentId();
  const agentFilter = activeAgentId;
  const { data: threads = [] } = useThreads(
    undefined,
    currentMode,
    agentFilter,
  );
  const { data: projects = [] } = useProjects();
  const { data: threadProjectMap = {} } = useThreadMap();
  const { mutate: deleteThread } = useDeleteThread();
  const { mutate: renameThread } = useRenameThread();
  const { mutate: moveThreadToProject } = useMoveThreadToProject();
  const ensureProjectHome = useEnsureProjectHome();
  const { mutate: deleteProject, isPending: isDeletingProject } =
    useDeleteProject();

  const [createProjectDialogOpen, setCreateProjectDialogOpen] = useState(false);
  const [renameDialogOpen, setRenameDialogOpen] = useState(false);
  const [renameThreadId, setRenameThreadId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [projectToDelete, setProjectToDelete] = useState<Project | null>(null);
  const [threadToDelete, setThreadToDelete] = useState<AgentThread | null>(
    null,
  );

  const handleOpenProject = useCallback(
    (project: Project) => {
      ensureProjectHome.mutate(project, {
        onSuccess: ({ threadId }) =>
          navigate(`/workspace/realtime/${encodeURIComponent(threadId)}`, {
            state: { openProjectWorkbench: true },
          }),
        onError: () => toast.error("项目工作群打开失败，请重试"),
      });
    },
    [ensureProjectHome, navigate],
  );

  const handleDelete = useCallback(
    (threadId: string) => {
      deleteThread({ threadId });
      if (threadId === threadIdFromPath) {
        const threadIndex = threads.findIndex((x) => x.thread_id === threadId);
        let nextThreadId = "new";
        if (threadIndex > -1) {
          if (threads[threadIndex + 1]) {
            nextThreadId = threads[threadIndex + 1]!.thread_id;
          } else if (threads[threadIndex - 1]) {
            nextThreadId = threads[threadIndex - 1]!.thread_id;
          }
        }
        void navigate(`/workspace/realtime/${nextThreadId}`);
      }
    },
    [deleteThread, navigate, threadIdFromPath, threads],
  );

  const handleRenameClick = useCallback(
    (threadId: string, currentTitle: string) => {
      setRenameThreadId(threadId);
      setRenameValue(currentTitle);
      setRenameDialogOpen(true);
    },
    [],
  );

  const handleRenameSubmit = useCallback(() => {
    if (renameThreadId && renameValue.trim()) {
      renameThread({ threadId: renameThreadId, title: renameValue.trim() });
      setRenameDialogOpen(false);
      setRenameThreadId(null);
      setRenameValue("");
    }
  }, [renameThread, renameThreadId, renameValue]);

  const handleProjectDelete = useCallback(() => {
    if (!projectToDelete) return;
    deleteProject(projectToDelete.id, {
      onSuccess: () => setProjectToDelete(null),
    });
  }, [deleteProject, projectToDelete]);

  const handleThreadDelete = useCallback(() => {
    if (!threadToDelete) return;
    handleDelete(threadToDelete.thread_id);
    setThreadToDelete(null);
  }, [handleDelete, threadToDelete]);

  const handleShare = useCallback(
    async (threadId: string) => {
      const VERCEL_URL = "https://echo-v2.vercel.app";
      const isLocalhost =
        window.location.hostname === "localhost" ||
        window.location.hostname === "127.0.0.1";
      const baseUrl = isLocalhost ? VERCEL_URL : window.location.origin;
      const shareUrl = `${baseUrl}/workspace/realtime/${threadId}`;
      try {
        await copyTextToClipboard(shareUrl);
        toast.success(t.clipboard.linkCopied);
      } catch {
        toast.error(t.clipboard.failedToCopyToClipboard);
      }
    },
    [t],
  );

  const handleExport = useCallback(
    async (thread: AgentThread, format: "markdown" | "json") => {
      try {
        const apiClient = getAPIClient();
        const state = await apiClient.threads.getState<AgentThreadState>(
          thread.thread_id,
        );
        const messages = state.values?.messages ?? [];
        if (messages.length === 0) {
          toast.error(t.conversation.noMessages);
          return;
        }
        if (format === "markdown") {
          exportThreadAsMarkdown(thread, messages);
        } else {
          exportThreadAsJSON(thread, messages);
        }
        toast.success(t.common.exportSuccess);
      } catch {
        toast.error(t.toolCalls.toastExportConversationFailed);
      }
    },
    [t],
  );

  // Group threads by project (mode-filtered).
  // Chat mode: flat Recents. Code/collaboration work: per-project groups
  // plus uncategorized Recents.
  const groups = useMemo<ThreadGroup[]>(() => {
    const recentsLabel = !env.STATIC_WEBSITE_ONLY
      ? t.sidebar.recentChats
      : t.sidebar.demoChats;

    // Chat mode → flat list, no per-project grouping.
    if (currentMode === undefined) {
      return [{ key: "recents", label: recentsLabel, threads }];
    }

    const byProject = new Map<string, AgentThread[]>();
    const uncategorized: AgentThread[] = [];

    for (const thread of threads) {
      const projectId = threadProjectMap[thread.thread_id];
      const project = projectId
        ? projects.find((p) => p.id === projectId)
        : undefined;
      if (project) {
        if (!byProject.has(project.id)) byProject.set(project.id, []);
        byProject.get(project.id)!.push(thread);
      } else {
        uncategorized.push(thread);
      }
    }

    // Keep empty projects visible. Otherwise a freshly created project vanishes
    // from the sidebar until its first task is moved into it, which looks like
    // the create action failed.
    const projectGroups: ThreadGroup[] = projects.map((p) => ({
      key: `project:${p.id}`,
      label: p.name,
      icon: p.icon,
      project: p,
      threads: byProject.get(p.id) ?? [],
    }));

    const result: ThreadGroup[] = [...projectGroups];
    if (uncategorized.length > 0 || projectGroups.length === 0) {
      result.push({
        key: "recents",
        label: recentsLabel,
        threads: uncategorized,
      });
    }
    return result;
  }, [currentMode, threads, projects, threadProjectMap, t]);

  const renderThreadItem = (thread: AgentThread) => {
    const href = routeForThread(thread);
    const isActive = href === pathname;
    return (
      <SidebarMenuItem key={thread.thread_id} className="group/side-menu-item">
        <SidebarMenuButton isActive={isActive} asChild>
          <Link
            to={href}
            state={{
              threadOwnerAgentId: ownerAgentForThread(thread) || undefined,
              workspacePath: workspacePathForThread(thread) || undefined,
            }}
            title={titleOfThread(thread)}
            onMouseDown={() => {
              const owner = ownerAgentForThread(thread);
              if (owner) emitAgentChanged(owner, "thread");
            }}
          >
            <span>{titleOfThread(thread)}</span>
          </Link>
        </SidebarMenuButton>
        {!env.STATIC_WEBSITE_ONLY && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <SidebarMenuAction
                showOnHover
                className="bg-background/50 hover:bg-background"
              >
                <MoreHorizontal />
                <span className="sr-only">{t.common.more}</span>
              </SidebarMenuAction>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              className="w-48 rounded-lg"
              side="right"
              align="start"
            >
              <DropdownMenuItem
                onSelect={() =>
                  handleRenameClick(thread.thread_id, titleOfThread(thread))
                }
              >
                <Pencil className="text-muted-foreground" />
                <span>{t.common.rename}</span>
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => handleShare(thread.thread_id)}>
                <Share2 className="text-muted-foreground" />
                <span>{t.common.share}</span>
              </DropdownMenuItem>
              <DropdownMenuSub>
                <DropdownMenuSubTrigger>
                  <Download className="text-muted-foreground" />
                  <span>{t.common.export}</span>
                </DropdownMenuSubTrigger>
                <DropdownMenuSubContent>
                  <DropdownMenuItem
                    onSelect={() => handleExport(thread, "markdown")}
                  >
                    <FileText className="text-muted-foreground" />
                    <span>{t.common.exportAsMarkdown}</span>
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onSelect={() => handleExport(thread, "json")}
                  >
                    <FileJson className="text-muted-foreground" />
                    <span>{t.common.exportAsJSON}</span>
                  </DropdownMenuItem>
                </DropdownMenuSubContent>
              </DropdownMenuSub>
              <DropdownMenuSub>
                <DropdownMenuSubTrigger>
                  <FolderIcon className="text-muted-foreground" />
                  <span>{t.sidebar.moveToProject}</span>
                </DropdownMenuSubTrigger>
                <DropdownMenuSubContent>
                  {projects.map((project) => (
                    <DropdownMenuItem
                      key={project.id}
                      onSelect={() =>
                        moveThreadToProject({
                          threadId: thread.thread_id,
                          projectId: project.id,
                        })
                      }
                    >
                      <span className="mr-1">{project.icon}</span>
                      <span>{project.name}</span>
                    </DropdownMenuItem>
                  ))}
                  {projects.length > 0 && <DropdownMenuSeparator />}
                  <DropdownMenuItem
                    onSelect={() => setCreateProjectDialogOpen(true)}
                  >
                    <PlusIcon className="text-muted-foreground" />
                    <span>{t.sidebar.newProject}</span>
                  </DropdownMenuItem>
                </DropdownMenuSubContent>
              </DropdownMenuSub>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                variant="destructive"
                onSelect={() => setThreadToDelete(thread)}
              >
                <Trash2 />
                <span>{t.common.delete}</span>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </SidebarMenuItem>
    );
  };

  if (threads.length === 0 && projects.length === 0) {
    return null;
  }

  return (
    <>
      {groups.map((group, idx) => {
        // First group gets the "+ New project" button to create a project.
        const showNewProjectButton = idx === 0;
        return (
          <Collapsible
            defaultOpen
            key={group.key}
            className={group.project ? "group/project-group" : "group/recent"}
          >
            <SidebarGroup className="pt-0">
              <SidebarGroupLabel className="flex h-6 items-center gap-1 px-1.5 text-xs">
                <CollapsibleTrigger
                  className="flex size-5 shrink-0 items-center justify-center rounded hover:bg-muted"
                  aria-label={`${group.label} 展开或收起`}
                >
                  <ChevronDownIcon
                    className={
                      group.project
                        ? "size-3 transition-transform group-data-[state=closed]/project-group:-rotate-90"
                        : "size-3 transition-transform group-data-[state=closed]/recent:-rotate-90"
                    }
                  />
                </CollapsibleTrigger>
                {group.project ? (
                  <button
                    type="button"
                    className="flex min-w-0 flex-1 items-center gap-1 text-left hover:text-foreground disabled:opacity-60"
                    onClick={() => handleOpenProject(group.project!)}
                    disabled={
                      ensureProjectHome.isPending &&
                      ensureProjectHome.variables?.id === group.project.id
                    }
                    title={`打开项目工作群：${group.label}`}
                  >
                    {group.icon && <span>{group.icon}</span>}
                    <span className="truncate">{group.label}</span>
                  </button>
                ) : (
                  <span className="min-w-0 flex-1 truncate">{group.label}</span>
                )}
                {showNewProjectButton && (
                  <button
                    type="button"
                    onClick={() => setCreateProjectDialogOpen(true)}
                    className="text-muted-foreground hover:text-foreground transition-colors"
                    title={t.sidebar.newProject}
                    aria-label={t.sidebar.newProject}
                  >
                    <PlusIcon className="size-4" />
                  </button>
                )}
                {group.project && !showNewProjectButton && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button
                        type="button"
                        className="text-muted-foreground hover:text-foreground transition-colors"
                        title={t.common.more}
                        aria-label={`${t.common.more}: ${group.label}`}
                      >
                        <MoreHorizontal className="size-4" />
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="start" side="right">
                      <DropdownMenuItem
                        variant="destructive"
                        onSelect={() => setProjectToDelete(group.project!)}
                      >
                        <Trash2 />
                        {t.common.delete}
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
              </SidebarGroupLabel>
              <CollapsibleContent>
                <SidebarGroupContent className="group-data-[collapsible=icon]:pointer-events-none group-data-[collapsible=icon]:-mt-8 group-data-[collapsible=icon]:opacity-0">
                  <SidebarMenu>
                    <div className="flex w-full flex-col gap-1">
                      {group.threads.map(renderThreadItem)}
                    </div>
                  </SidebarMenu>
                </SidebarGroupContent>
              </CollapsibleContent>
            </SidebarGroup>
          </Collapsible>
        );
      })}

      <Dialog open={renameDialogOpen} onOpenChange={setRenameDialogOpen}>
        <DialogContent className="sm:max-w-[var(--dialog-md)]">
          <DialogHeader>
            <DialogTitle>{t.common.rename}</DialogTitle>
          </DialogHeader>
          <div className="py-4">
            <Input
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              placeholder={t.common.rename}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !isIMEComposing(e)) {
                  e.preventDefault();
                  handleRenameSubmit();
                }
              }}
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setRenameDialogOpen(false)}
            >
              {t.common.cancel}
            </Button>
            <Button onClick={handleRenameSubmit}>{t.common.save}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={projectToDelete !== null}
        onOpenChange={(open) => !open && setProjectToDelete(null)}
      >
        <DialogContent className="sm:max-w-[var(--dialog-md)]">
          <DialogHeader>
            <DialogTitle>{t.sidebar.confirmDeleteProjectTitle}</DialogTitle>
            <DialogDescription>
              {projectToDelete
                ? t.sidebar.confirmDeleteProject(projectToDelete.name)
                : null}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setProjectToDelete(null)}>
              {t.common.cancel}
            </Button>
            <Button
              variant="destructive"
              onClick={handleProjectDelete}
              disabled={isDeletingProject}
            >
              {t.common.delete}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={threadToDelete !== null}
        onOpenChange={(open) => !open && setThreadToDelete(null)}
      >
        <DialogContent className="sm:max-w-[var(--dialog-md)]">
          <DialogHeader>
            <DialogTitle>{t.common.delete}</DialogTitle>
            <DialogDescription>
              {threadToDelete
                ? t.sidebar.confirmDeleteThread(titleOfThread(threadToDelete))
                : null}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setThreadToDelete(null)}>
              {t.common.cancel}
            </Button>
            <Button variant="destructive" onClick={handleThreadDelete}>
              {t.common.delete}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <CreateProjectDialog
        open={createProjectDialogOpen}
        onOpenChange={setCreateProjectDialogOpen}
      />
    </>
  );
}
