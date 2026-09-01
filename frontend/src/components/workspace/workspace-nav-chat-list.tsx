import {
  ChevronDownIcon,
  FolderIcon,
  MoreHorizontal,
  PlusIcon,
  SparklesIcon,
  Trash2,
} from "lucide-react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useState, useEffect } from "react";
import { toast } from "sonner";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { useI18n } from "@/core/i18n/hooks";
import {
  type Project,
  useDeleteProject,
  useEnsureProjectHome,
  useProjects,
} from "@/core/projects/hooks";
import { useFeatureSeen } from "@/hooks/use-feature-seen";

import { CreateProjectDialog } from "./create-project-dialog";

function ProjectCollapsible({
  projects,
  onCreateClick,
  onDeleteProject,
  onOpenProject,
  openingProjectId,
  t,
}: {
  projects: Project[];
  onCreateClick: () => void;
  onDeleteProject: (id: string) => void;
  onOpenProject: (project: Project) => void;
  openingProjectId?: string | null;
  t: { common: { delete: string }; sidebar: { projects: string } };
}) {
  return (
    <Collapsible defaultOpen className="group/projects">
      <SidebarGroup className="pt-0">
        <SidebarGroupLabel className="flex h-6 items-center justify-between px-1.5 text-xs">
          <CollapsibleTrigger className="flex items-center gap-1">
            <ChevronDownIcon className="size-3 transition-transform group-data-[state=closed]/projects:-rotate-90" />
            <span>{t.sidebar.projects}</span>
          </CollapsibleTrigger>
          <button
            onClick={onCreateClick}
            className="text-muted-foreground hover:text-foreground transition-colors"
            title={`${t.sidebar.projects}+`}
          >
            <PlusIcon className="size-4" />
          </button>
        </SidebarGroupLabel>
        <CollapsibleContent>
          <SidebarMenu>
            {projects.map((project) => (
              <SidebarMenuItem
                key={project.id}
                className="group-data-[collapsible=icon]:px-0 px-1.5"
              >
                <SidebarMenuButton
                  className="text-muted-foreground text-sm"
                  disabled={openingProjectId === project.id}
                  onClick={() => onOpenProject(project)}
                  title={`打开项目工作群：${project.name}`}
                >
                  <FolderIcon className="size-4" />
                  <span className="truncate">
                    {project.icon} {project.name}
                  </span>
                </SidebarMenuButton>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <SidebarMenuAction>
                      <MoreHorizontal className="size-4" />
                    </SidebarMenuAction>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" side="right">
                    <DropdownMenuItem
                      variant="destructive"
                      onSelect={() => onDeleteProject(project.id)}
                    >
                      <Trash2 />
                      {t.common.delete}
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </CollapsibleContent>
      </SidebarGroup>
    </Collapsible>
  );
}

export function WorkspaceNavChatList({
  showProjects = true,
  showOnlyProjects = false,
}: {
  showProjects?: boolean;
  showOnlyProjects?: boolean;
}) {
  const { t } = useI18n();
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const { data: projects = [] } = useProjects();
  const { mutate: deleteProject } = useDeleteProject();
  const ensureProjectHome = useEnsureProjectHome();
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const skillsSeen = useFeatureSeen(
    "skills",
    pathname === "/workspace/agents" &&
      new URLSearchParams(search).get("tab") === "skills",
  );
  const skillsTabActive =
    pathname === "/workspace/agents" &&
    new URLSearchParams(search).get("tab") === "skills";

  useEffect(() => {
    setMounted(true);
  }, []);

  const openProject = (project: Project) => {
    ensureProjectHome.mutate(project, {
      onSuccess: ({ threadId }) =>
        navigate(`/workspace/realtime/${encodeURIComponent(threadId)}`, {
          state: { openProjectWorkbench: true },
        }),
      onError: () => toast.error("项目工作群打开失败，请重试"),
    });
  };

  // Only render projects section
  if (showOnlyProjects) {
    return (
      <>
        {mounted && (
          <ProjectCollapsible
            projects={projects}
            onCreateClick={() => setCreateDialogOpen(true)}
            onDeleteProject={(id) => deleteProject(id)}
            onOpenProject={openProject}
            openingProjectId={
              ensureProjectHome.isPending
                ? ensureProjectHome.variables?.id
                : null
            }
            t={t}
          />
        )}
        <CreateProjectDialog
          open={createDialogOpen}
          onOpenChange={setCreateDialogOpen}
        />
      </>
    );
  }

  return (
    <>
      <SidebarGroup className="pt-0">
        <SidebarMenu>
          <SidebarMenuItem className="group-data-[collapsible=icon]:px-0 px-1.5 mt-0">
            <SidebarMenuButton
              isActive={skillsTabActive}
              asChild
              className="text-muted-foreground rounded-lg py-1 text-sm transition-colors duration-fast hover:bg-muted hover:text-foreground data-[active=true]:bg-muted data-[active=true]:text-foreground data-[active=true]:font-medium"
            >
              <Link to="/workspace/agents?surface=chat&tab=skills">
                <SparklesIcon className="size-[15px]" />
                <span className="flex items-center gap-1.5">
                  {t.sidebar.skills}
                  {!skillsSeen && (
                    <span className="rounded bg-primary/10 px-1 py-0.5 text-xs font-medium leading-none text-primary">
                      NEW
                    </span>
                  )}
                </span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarGroup>

      {/* Projects below nav links when showProjects is true */}
      {showProjects && mounted && (
        <ProjectCollapsible
          projects={projects}
          onCreateClick={() => setCreateDialogOpen(true)}
          onDeleteProject={(id) => deleteProject(id)}
          onOpenProject={openProject}
          openingProjectId={
            ensureProjectHome.isPending ? ensureProjectHome.variables?.id : null
          }
          t={t}
        />
      )}

      <CreateProjectDialog
        open={createDialogOpen}
        onOpenChange={setCreateDialogOpen}
      />
    </>
  );
}
