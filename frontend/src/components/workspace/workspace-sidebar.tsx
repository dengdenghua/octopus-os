import {
  ArrowUpDownIcon,
  BotIcon,
  BrainIcon,
  BriefcaseIcon,
  Building2Icon,
  CalendarDaysIcon,
  CheckCircle2Icon,
  ChevronRightIcon,
  Code2Icon,
  DatabaseIcon,
  DnaIcon,
  FolderIcon,
  FolderPlusIcon,
  GlobeIcon,
  MessageSquarePlusIcon,
  NetworkIcon,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon,
  PlusIcon,
  PuzzleIcon,
  Trash2Icon,
  type LucideIcon,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { swallow } from "@/core/utils/log";
import { uuid } from "@/core/utils/uuid";
import { useEvent, eventBus, emitProjectsChanged } from "@/core/events";

import { SettingsDialog } from "./settings";
import { AgentFooter, TeamFooter } from "./sidebar-footer";

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
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { getAPIClient } from "@/core/api";
import { getBackendBaseURL } from "@/core/config";
import { withAgentAvatarVersion } from "@/core/agents/avatar";
import { useI18n } from "@/core/i18n/hooks";
import { useDeleteThread, useThreads } from "@/core/threads/hooks";
import type { AgentThread } from "@/core/threads/types";
import { useActiveAgentId } from "@/core/agents/active";
import { formatRelativeTimestamp } from "@/core/utils/datetime";
import { basename, isAbsolutePath } from "@/lib/path-utils";
import { cn } from "@/lib/utils";
import { useCompanyEnabled } from "@/appliance/company-capability";

// Surface modes in the left sidebar. Chat and Company are handled by a
// dedicated two-panel switch so they feel like peer work surfaces instead
// of ordinary navigation rows. Team also lives under the Company surface.
// NOTE: label bags are now built inside the component through useI18n
// so translations respect the selected locale. Module-level constants
// capture just the route + icon.
type NavRoute = {
  to: string;
  icon: LucideIcon;
  labelKey?: string;
  label?: string;
};
const PRIMARY_WORKSPACE_ROUTE = "/workspace/realtime/new";
const COMPANY_WORKSPACE_ROUTE = "/workspace/company";

const CHAT_CAPABILITY_ROUTES: NavRoute[] = [
  {
    to: "/workspace/plugins?surface=chat",
    labelKey: "navPlugins",
    icon: PuzzleIcon,
  },
  {
    to: "/workspace/knowledge?surface=chat",
    labelKey: "navKnowledgeGraph",
    icon: DatabaseIcon,
  },
  {
    to: "/workspace/intelligence?surface=chat",
    labelKey: "navIntelligence",
    icon: BrainIcon,
  },
  {
    to: "/workspace/evolution?surface=chat",
    labelKey: "navEvolution",
    icon: DnaIcon,
  },
];

const COMPANY_ORG_ROUTES: NavRoute[] = [
  { to: COMPANY_WORKSPACE_ROUTE, label: "工作台", icon: BriefcaseIcon },
  {
    to: "/workspace/company/projects",
    label: "项目",
    icon: Building2Icon,
  },
  {
    to: "/workspace/company/tasks",
    label: "任务",
    icon: CheckCircle2Icon,
  },
  {
    to: "/workspace/company/milestones",
    label: "里程碑",
    icon: CalendarDaysIcon,
  },
  {
    to: "/workspace/company/ai",
    label: "AI 助手",
    icon: BotIcon,
  },
  { to: "/workspace/team/new", labelKey: "navTeam", icon: NetworkIcon },
  { to: "/workspace/agents", labelKey: "navHR", icon: BriefcaseIcon },
];

const COMPANY_CAPABILITY_ROUTES: NavRoute[] = [
  {
    to: "/workspace/intelligence?surface=company",
    labelKey: "navIntelligence",
    icon: BrainIcon,
  },
  {
    to: "/workspace/evolution?surface=company",
    labelKey: "navEvolution",
    icon: DnaIcon,
  },
  {
    to: "/workspace/knowledge?surface=company",
    labelKey: "navKnowledgeGraph",
    icon: DatabaseIcon,
  },
  {
    to: "/workspace/plugins?surface=company",
    labelKey: "navPlugins",
    icon: PuzzleIcon,
  },
];

type ThreadSummary = {
  id: string;
  title: string;
  updatedAt: string;
  mode: string;
  href: string;
  /** Agent ids associated with this thread · drives the WeChat-style
   *  avatar (single big avatar OR 2×2 / 3×3 grid for team threads). */
  agents: string[];
};

/** Pull a list of agent ids out of thread metadata · accepts the
 *  several places the backend stashes them (single agent on solo
 *  threads, agent_roster on team threads, fallback to bare ``agent``
 *  field). */
function isProjectThreadMode(mode: string): boolean {
  return mode === "code";
}

function isConversationThreadMode(mode: string): boolean {
  return (
    mode === "chat" ||
    mode === "chats" ||
    mode === "react" ||
    mode === "deep" ||
    // Legacy persisted swarm threads stay visible in the conversation list,
    // but the composer no longer exposes swarm as a selectable mode.
    mode === "swarm" ||
    mode === "agent"
  );
}

function deriveThreadAgents(meta: Record<string, unknown>): string[] {
  // 1. team threads · ``agent_roster`` is an array of {agent_id, ...}
  const roster = meta["agent_roster"];
  if (Array.isArray(roster)) {
    const ids = roster
      .map((r) =>
        r &&
        typeof r === "object" &&
        typeof (r as { agent_id?: unknown }).agent_id === "string"
          ? (r as { agent_id: string }).agent_id
          : null,
      )
      .filter((x): x is string => !!x);
    if (ids.length > 0) return ids;
  }
  // 2. team_members (legacy field name · same shape)
  const members = meta["team_members"];
  if (Array.isArray(members)) {
    const ids = members
      .map((r) =>
        typeof r === "string"
          ? r
          : r &&
              typeof r === "object" &&
              typeof (r as { agent_id?: unknown }).agent_id === "string"
            ? (r as { agent_id: string }).agent_id
            : null,
      )
      .filter((x): x is string => !!x);
    if (ids.length > 0) return ids;
  }
  // 3. solo agent · the ``agent`` field is set on every chat/code
  //    thread by the compat router (cf. metadata.agent='coder')
  const single = meta["agent"];
  if (typeof single === "string" && single.trim()) {
    return [single.trim()];
  }
  return [];
}

function cleanDisplayText(value: unknown): string {
  if (typeof value !== "string") return "";
  const s = value
    .trim()
    .replace(/[\x00-\x1F\x7F-\x9F]/g, "")
    .replace(/\s+/g, " ");
  const questionMarks = (s.match(/\?/g) ?? []).length;
  const replacementChars = (s.match(/\uFFFD/g) ?? []).length;
  if (
    /^\?{3,}$/.test(s) ||
    (questionMarks >= 5 && questionMarks / s.length > 0.25) ||
    (replacementChars >= 3 && replacementChars / s.length > 0.2)
  ) {
    return "";
  }
  return s;
}

function threadTitleFromContent(content: unknown): string {
  if (typeof content === "string") return cleanDisplayText(content);
  if (!Array.isArray(content)) return "";
  return cleanDisplayText(
    content
      .map((part) => {
        if (typeof part === "string") return part;
        if (
          part &&
          typeof part === "object" &&
          (part as Record<string, unknown>).type === "text" &&
          typeof (part as Record<string, unknown>).text === "string"
        ) {
          return (part as { text: string }).text;
        }
        return "";
      })
      .filter(Boolean)
      .join(" "),
  );
}

function truncateThreadTitle(title: string): string {
  return title.length > 60 ? `${title.slice(0, 58)}...` : title;
}

/** Best-effort thread title from `values`: first user message content,
 *  truncated. Falls back to metadata.title or a short thread-id. */
function deriveThreadTitle(thread: {
  thread_id: string;
  metadata?: Record<string, unknown>;
  values?: Record<string, unknown>;
}): string {
  const metaTitle = cleanDisplayText(thread.metadata?.["title"]);
  if (metaTitle) return truncateThreadTitle(metaTitle);

  const messages = thread.values?.["messages"];
  if (Array.isArray(messages)) {
    for (const m of messages) {
      if (
        m &&
        typeof m === "object" &&
        (m as Record<string, unknown>).type === "human"
      ) {
        const content = (m as Record<string, unknown>).content;
        const title = threadTitleFromContent(content);
        if (title) return truncateThreadTitle(title);
      }
    }
  }
  const valuesTitle = cleanDisplayText(thread.values?.["title"]);
  if (valuesTitle && valuesTitle !== "New chat" && valuesTitle !== "New task") {
    return truncateThreadTitle(valuesTitle);
  }
  return `thread/${thread.thread_id.slice(0, 6)}`;
}

function threadHref(thread: {
  thread_id: string;
  metadata?: Record<string, unknown>;
}) {
  const mode =
    typeof thread.metadata?.["mode"] === "string"
      ? (thread.metadata["mode"] as string)
      : "chats";
  if (mode === "react" || mode === "deep" || mode === "agent") {
    const agent =
      typeof thread.metadata?.["agent"] === "string"
        ? thread.metadata["agent"].trim()
        : typeof thread.metadata?.["agent_name"] === "string"
          ? thread.metadata["agent_name"].trim()
          : "";
    if (agent) {
      return `/workspace/agents/${encodeURIComponent(agent)}/chats/${thread.thread_id}`;
    }
  }
  const segment = mode === "team" ? "team" : "realtime";
  return `/workspace/${segment}/${thread.thread_id}`;
}

const PROJECTS_KEY = "octopus.projects";
const RECENT_WORKDIRS_KEY = "octopus:recentWorkdirs";
const PROJECT_GROUPING_KEY = "octopus.sidebar.project-grouping-enabled";

function readUserProjects(): string[] {
  try {
    const raw = window.localStorage.getItem(PROJECTS_KEY);
    if (!raw) return [];
    const data = JSON.parse(raw);
    return Array.isArray(data) ? (data as string[]).filter(Boolean) : [];
  } catch (e) {
    swallow(e);
    return [];
  }
}

function writeUserProjects(names: string[]) {
  try {
    window.localStorage.setItem(PROJECTS_KEY, JSON.stringify(names));
  } catch (e) {
    swallow(e, "storage");
  }
}

function rememberProjectWorkDir(path: string) {
  if (typeof window === "undefined" || !isAbsolutePath(path)) return;
  try {
    const raw = window.localStorage.getItem(RECENT_WORKDIRS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    const current = Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === "string")
      : [];
    const normalize = (value: string) =>
      value.trim().replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
    const next = [
      path,
      ...current.filter((item) => normalize(item) !== normalize(path)),
    ].slice(0, 6);
    window.localStorage.setItem(RECENT_WORKDIRS_KEY, JSON.stringify(next));
  } catch (e) {
    swallow(e, "storage");
  }
}

function emitWorkDirSelected(path: string) {
  if (typeof window === "undefined" || !isAbsolutePath(path)) return;
  rememberProjectWorkDir(path);
  window.dispatchEvent(
    new CustomEvent("octopus:workdir-selected", {
      detail: { path, source: "projects" },
    }),
  );
}

function readProjectGroupingEnabled(): boolean {
  try {
    const raw = window.localStorage.getItem(PROJECT_GROUPING_KEY);
    return raw === null ? true : raw === "1";
  } catch (e) {
    swallow(e);
    return true;
  }
}

export function WorkspaceSidebar(props: React.ComponentProps<typeof Sidebar>) {
  const { pathname, search } = useLocation();
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const apiClient = useMemo(() => getAPIClient(), []);

  // Resolve a NavRoute's labelKey against the sidebar namespace. Every
  // key we reference from the routes arrays is declared as a string on
  // the Translations interface · the cast keeps TS narrowing happy
  // without silently swallowing typos.
  const resolveLabel = useCallback(
    (key: string) =>
      (t.sidebar as unknown as Record<string, string>)[key] ?? key,
    [t],
  );
  const resolveRoutes = useCallback(
    (routes: NavRoute[]) =>
      routes.map((r) => ({
        ...r,
        label: r.label ?? (r.labelKey ? resolveLabel(r.labelKey) : r.to),
      })),
    [resolveLabel],
  );
  const companyOrgItems = useMemo(
    () => resolveRoutes(COMPANY_ORG_ROUTES),
    [resolveRoutes],
  );
  const chatCapabilityItems = useMemo(
    () => resolveRoutes(CHAT_CAPABILITY_ROUTES),
    [resolveRoutes],
  );
  const companyCapabilityItems = useMemo(
    () => resolveRoutes(COMPANY_CAPABILITY_ROUTES),
    [resolveRoutes],
  );

  // Settings dialog state
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsDefaultSection, setSettingsDefaultSection] = useState<
    "appearance" | "memory" | "notification" | "about" | "account" | "models"
  >("appearance");

  // Listen for open-settings event via EventBus
  useEvent("ui:open-settings", (payload) => {
    if (payload.tab) {
      const next =
        payload.tab === "account" ||
        payload.tab === "appearance" ||
        payload.tab === "models" ||
        payload.tab === "memory" ||
        payload.tab === "notification" ||
        payload.tab === "about"
          ? payload.tab
          : "appearance";
      setSettingsDefaultSection(next);
    }
    setSettingsOpen(true);
  });

  // Thread list is scoped to the currently-active agent (localStorage-
  // backed, `octopus.active-agent` key). Switching agent in the footer
  // dropdown updates localStorage + dispatches `octopus:active-agent`
  // · we subscribe here so the list refetches with the new scope.
  // Previously the sidebar showed ALL threads across all agents, so
  // switching from Coder to a specialist kept showing Coder sessions
  // — defeating the "per-agent history" that the backend already
  // maintains (agents/<id>/sessions/<thread>.jsonl).
  const activeAgentId = useActiveAgentId();

  // Two thread feeds:
  //   1. ``rawThreads`` — chat-mode threads, filtered by activeAgentId
  // Implementation note.
  //   2. ``rawCodeThreads`` — code project threads, NOT filtered by
  //      agent · code workspaces are workspace-level so they should show up
  //      regardless of which agent the user has selected. Without this
  // Implementation note.
  //      section and the user would think their projects vanished.
  const { data: rawThreads } = useThreads(
    {
      limit: 30,
      sortBy: "updated_at",
      sortOrder: "desc",
      select: ["thread_id", "updated_at", "values", "metadata"],
    },
    undefined,
    activeAgentId,
  );
  const { data: rawCodeThreads } = useThreads({
    limit: 30,
    sortBy: "updated_at",
    sortOrder: "desc",
    select: ["thread_id", "updated_at", "values", "metadata"],
    metadata: { mode: "code" },
  });
  // Merge the feeds into one list, deduping by thread_id. The
  // chat feed (filtered by agent) wins on overlap so its metadata
  // sticks · code feeds add cross-agent project threads on top.
  const mergedRaw = (() => {
    const m = new Map<string, AgentThread>();
    for (const t of rawThreads ?? []) m.set(t.thread_id, t);
    for (const t of rawCodeThreads ?? []) {
      if (!m.has(t.thread_id)) m.set(t.thread_id, t);
    }
    return Array.from(m.values()).sort((a, b) =>
      (b.updated_at || "").localeCompare(a.updated_at || ""),
    );
  })();

  const threads: ThreadSummary[] = mergedRaw.map((t) => ({
    id: t.thread_id,
    title: deriveThreadTitle(t),
    updatedAt: t.updated_at,
    mode:
      typeof t.metadata?.["mode"] === "string"
        ? (t.metadata["mode"] as string)
        : "chat",
    href: threadHref(t),
    agents: deriveThreadAgents((t.metadata ?? {}) as Record<string, unknown>),
  }));

  // User-defined projects (localStorage) — so an empty project still
  // shows in the sidebar before any threads are tagged with it.
  const [userProjects, setUserProjects] = useState<string[]>(() =>
    readUserProjects(),
  );
  useEffect(() => {
    const refresh = () => setUserProjects(readUserProjects());
    window.addEventListener("storage", refresh);
    window.addEventListener("octopus:projects-changed", refresh);
    return () => {
      window.removeEventListener("storage", refresh);
      window.removeEventListener("octopus:projects-changed", refresh);
    };
  }, []);
  const [projectGroupingEnabled, setProjectGroupingEnabled] = useState<boolean>(
    () => readProjectGroupingEnabled(),
  );
  useEffect(() => {
    try {
      window.localStorage.setItem(
        PROJECT_GROUPING_KEY,
        projectGroupingEnabled ? "1" : "0",
      );
    } catch (e) {
      swallow(e);
    }
  }, [projectGroupingEnabled]);
  const toggleProjectGrouping = useCallback(() => {
    setProjectGroupingEnabled((enabled) => !enabled);
  }, []);

  // "New project" flow:
  //   1. Try the native directory picker (Chromium) — lets the user
  //      either pick an existing folder or type a new folder name in
  // Implementation note.
  //   2. Fall back to a hidden <input type="file" webkitdirectory> in
  //      Safari / Firefox where showDirectoryPicker isn't available.
  //   3. Final fallback: an inline input row in the sidebar (used when
  //      even the file input is blocked, e.g. in sandboxed webviews).
  const folderInputRef = useRef<HTMLInputElement | null>(null);
  const [projectDraftOpen, setProjectDraftOpen] = useState(false);

  const saveProjectName = useCallback((name: string) => {
    const trimmed = name.trim();
    setProjectDraftOpen(false);
    if (!trimmed) return;
    const next = Array.from(new Set([trimmed, ...readUserProjects()]));
    writeUserProjects(next);
    setUserProjects(next);
    emitProjectsChanged();
  }, []);

  const saveProjectPath = useCallback(
    (path: string) => {
      const trimmed = path.trim();
      if (!trimmed || !isAbsolutePath(trimmed)) return;
      saveProjectName(basename(trimmed) || trimmed);
      emitWorkDirSelected(trimmed);
    },
    [saveProjectName],
  );

  const pickProjectFolder = useCallback(async () => {
    if (window.octopus?.dialog?.open) {
      try {
        const result = await window.octopus.dialog.open({
          properties: ["openDirectory", "createDirectory"],
        });
        const selected = result.filePaths[0];
        if (!result.canceled && selected) {
          saveProjectPath(selected);
          return;
        }
      } catch (err) {
        swallow(err);
      }
    }

    // 1. Native picker — supported in modern Chromium. Gives the user
    //    both "pick existing" and "new folder" in one OS dialog.
    type DirPicker = () => Promise<{ name: string }>;
    const picker = (window as unknown as { showDirectoryPicker?: DirPicker })
      .showDirectoryPicker;
    if (typeof picker === "function") {
      try {
        const handle = await picker();
        if (handle?.name) {
          saveProjectName(handle.name);
          return;
        }
      } catch (err) {
        swallow(err);
        // Cancelled or permission denied. AbortError is the normal
        // cancel path — fall back to the inline input only when the
        // picker refused to even open (TypeError, SecurityError).
        const name =
          err && typeof err === "object" && "name" in err
            ? (err as { name?: string }).name
            : "";
        if (name === "AbortError") return;
      }
    }

    // 2. webkitdirectory input — fallback for browsers without
    //    showDirectoryPicker. First segment of webkitRelativePath is
    //    the chosen folder's name.
    const input = folderInputRef.current;
    if (input) {
      input.value = "";
      input.click();
      return;
    }

    // 3. Inline draft row — last resort.
    setProjectDraftOpen(true);
  }, [saveProjectName, saveProjectPath]);

  useEffect(() => {
    const handler = () => void pickProjectFolder();
    window.addEventListener("octopus:project-new", handler);
    return () => window.removeEventListener("octopus:project-new", handler);
  }, [pickProjectFolder]);

  const onFolderInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      const rel =
        (file as File & { webkitRelativePath?: string }).webkitRelativePath ??
        "";
      const firstSegment = rel.split("/")[0] || file.name;
      saveProjectName(firstSegment);
    },
    [saveProjectName],
  );

  // Group threads by project · priority:
  //   1. explicit ``metadata.project`` (user-tagged)
  //   2. mode-derived synthetic project:
  //        code → workspace_path basename (the folder you're working in)
  //   3. fall through to "Recent" (only chat-mode threads land here)
  //
  // Why this matters: code conversations are about a *project*,
  // Implementation note.
  // ad-hoc questions and made it impossible to find which thread
  // belonged to which project. Now they cluster under their actual
  // Implementation note.
  const projectThreads = threads.filter((t) => isProjectThreadMode(t.mode));
  const conversationThreads = threads.filter((t) =>
    isConversationThreadMode(t.mode),
  );

  const byProject: Record<string, ThreadSummary[]> = {};
  const explicitProjectThreadIdsByProject: Record<string, string[]> = {};
  for (const name of userProjects) byProject[name] = [];
  const rawThreadMap = new Map(mergedRaw.map((r) => [r.thread_id, r]));
  for (const t of projectThreads) {
    const raw = rawThreadMap.get(t.id);
    const meta = (raw?.metadata ?? {}) as Record<string, unknown>;
    const explicitProject = cleanDisplayText(meta["project"]);
    let project: string;
    if (explicitProject) {
      project = explicitProject;
      (explicitProjectThreadIdsByProject[project] ??= []).push(t.id);
    } else if (t.mode === "code") {
      const wp =
        typeof meta["workspace_path"] === "string"
          ? (meta["workspace_path"] as string).trim()
          : "";
      project = wp ? basename(wp) : "Code";
    } else {
      project = "Project";
    }
    (byProject[project] ??= []).push(t);
  }
  // Belt-and-suspenders · ensure Recent never contains non-chat
  const projectOrder = Object.keys(byProject);
  const deletableProjects = new Set<string>();
  for (const project of Object.keys(explicitProjectThreadIdsByProject)) {
    deletableProjects.add(project);
  }
  for (const project of userProjects) {
    const hasExplicitThreads =
      (explicitProjectThreadIdsByProject[project]?.length ?? 0) > 0;
    const isEmptyUserProject = (byProject[project]?.length ?? 0) === 0;
    if (hasExplicitThreads || isEmptyUserProject) {
      deletableProjects.add(project);
    }
  }
  const [deletingProject, setDeletingProject] = useState<string | null>(null);

  const deleteProject = async (project: string) => {
    const threadIds = explicitProjectThreadIdsByProject[project] ?? [];
    const next = readUserProjects().filter((p) => p !== project);

    if (threadIds.length === 0) {
      writeUserProjects(next);
      setUserProjects(next);
      window.dispatchEvent(new Event("octopus:projects-changed"));
      return;
    }

    setDeletingProject(project);
    try {
      await Promise.all(
        threadIds.map((threadId) =>
          apiClient.threads.updateState(threadId, {
            metadata: { project: "" },
          }),
        ),
      );
      writeUserProjects(next);
      setUserProjects(next);
      emitProjectsChanged();
      queryClient.setQueriesData(
        { queryKey: ["threads", "search"], exact: false },
        (oldData: AgentThread[] | undefined) => {
          if (!oldData) return oldData;
          const ids = new Set(threadIds);
          return oldData.map((thread) =>
            ids.has(thread.thread_id)
              ? {
                  ...thread,
                  metadata: {
                    ...(thread.metadata ?? {}),
                    project: "",
                  },
                }
              : thread,
          );
        },
      );
    } catch (error) {
      console.error("Failed to delete project", error);
    } finally {
      setDeletingProject(null);
      void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
    }
  };
  // OS appliance 默认剥离 company(PM 交给企业版插件)→ 隐藏"工作"surface 入口。
  const companyEnabled = useCompanyEnabled();
  const surfaceParam = new URLSearchParams(search).get("surface");
  const companySurfaceActive =
    companyEnabled &&
    (surfaceParam === "company" ||
      (surfaceParam !== "chat" && isCompanySurfaceRoute(pathname)));
  const sidebarConversationThreads = conversationThreads;

  return (
    <Sidebar
      variant="sidebar"
      collapsible="icon"
      className="border-r border-border/40 bg-background/82 backdrop-blur"
      {...props}
    >
      {/* Implementation note. */}
      <SidebarHeader className="relative h-11 shrink-0 items-center justify-center border-b border-border/45 bg-sidebar/65 px-2.5 py-0 group-data-[collapsible=icon]:h-20 group-data-[collapsible=icon]:justify-end group-data-[collapsible=icon]:pb-2 group-data-[collapsible=icon]:pt-2">
        <WorkspaceSurfaceSwitch
          active={companySurfaceActive ? "work" : "agent"}
          showWork={companyEnabled}
        />
        <div className="absolute right-0.5 top-1/2 -translate-y-1/2 group-data-[collapsible=icon]:left-1/2 group-data-[collapsible=icon]:right-auto group-data-[collapsible=icon]:top-2 group-data-[collapsible=icon]:-translate-x-[calc(50%+4px)] group-data-[collapsible=icon]:translate-y-0">
          <CollapseToggle compact />
        </div>
      </SidebarHeader>

      {/* Tight body: px-1.5 py-1.5 instead of default p-2/px-2 so groups
          sit closer to the header and we win a few rows of vertical
          space back. */}
      <SidebarContent className="gap-1.5 px-2.5 py-2">
        <SurfaceCreateButton companySurfaceActive={companySurfaceActive} />
        {companySurfaceActive ? (
          <>
            <NavSection
              label={t.sidebar.navSwarm}
              items={companyOrgItems}
              pathname={pathname}
            />
            <NavSection items={companyCapabilityItems} pathname={pathname} />
          </>
        ) : (
          <>
            <NavSection items={chatCapabilityItems} pathname={pathname} />
            <ProjectsSection
              groups={projectOrder}
              byProject={byProject}
              pathname={pathname}
              draftOpen={projectDraftOpen}
              deletableProjects={deletableProjects}
              deletingProject={deletingProject}
              groupingEnabled={projectGroupingEnabled}
              onDraftCommit={saveProjectName}
              onDraftCancel={() => setProjectDraftOpen(false)}
              onDeleteProject={deleteProject}
              onToggleGrouping={toggleProjectGrouping}
            />
            <ChatsSection
              threads={sidebarConversationThreads}
              pathname={pathname}
              label={t.sidebar.sectionChats}
            />
          </>
        )}
        {/* Hidden directory input — used as the Safari/Firefox fallback
            when showDirectoryPicker is unavailable. webkitdirectory
            forces a folder selection instead of a single file. */}
        <input
          ref={folderInputRef}
          type="file"
          // @ts-expect-error — non-standard but supported across Chromium/WebKit
          webkitdirectory=""
          directory=""
          multiple
          hidden
          onChange={onFolderInputChange}
        />
      </SidebarContent>

      <SidebarFooter className="border-t border-border/40 p-1.5">
        {pathname.startsWith("/workspace/team") ? (
          <TeamFooter />
        ) : (
          <AgentFooter />
        )}
      </SidebarFooter>
      <SidebarRail />
      <SettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        defaultSection={settingsDefaultSection}
      />
    </Sidebar>
  );
}

type NavItem = NavRoute & { label: string };

function NavSection({
  items,
  pathname,
  label,
}: {
  items: NavItem[];
  pathname: string;
  label?: string;
}) {
  return (
    <SidebarGroup className="p-0 px-1 group-data-[collapsible=icon]:px-0">
      {label && (
        <div className="px-2 pb-1 pt-2 text-[11px] font-medium text-muted-foreground/72 group-data-[collapsible=icon]:sr-only">
          {label}
        </div>
      )}
      <SidebarMenu className="gap-0.5">
        {items.map((item) => (
          <NavRow key={item.to} item={item} pathname={pathname} />
        ))}
      </SidebarMenu>
    </SidebarGroup>
  );
}

function routePath(to: string): string {
  return to.split(/[?#]/)[0] || to;
}

function isNavRouteActive(pathname: string, to: string) {
  const path = routePath(to);
  if (path === PRIMARY_WORKSPACE_ROUTE) {
    return isChatSurfaceRoute(pathname);
  }
  if (path === COMPANY_WORKSPACE_ROUTE) {
    return pathname === COMPANY_WORKSPACE_ROUTE;
  }
  if (path === "/workspace/team/new") {
    return (
      pathname === "/workspace/team" || pathname.startsWith("/workspace/team/")
    );
  }
  if (path === "/workspace/agents" && isAgentChatRoute(pathname)) {
    return false;
  }
  return pathname === path || pathname.startsWith(`${path}/`);
}

function isAgentChatRoute(pathname: string) {
  return /^\/workspace\/agents\/[^/]+\/chats(?:\/|$)/.test(pathname);
}

function isChatSurfaceRoute(pathname: string) {
  return (
    pathname === "/workspace/realtime" ||
    pathname.startsWith("/workspace/realtime/") ||
    pathname === "/workspace/chats" ||
    pathname.startsWith("/workspace/chats/") ||
    isAgentChatRoute(pathname)
  );
}

function isCompanySurfaceRoute(pathname: string) {
  if (isAgentChatRoute(pathname)) return false;
  return (
    pathname === COMPANY_WORKSPACE_ROUTE ||
    pathname.startsWith(`${COMPANY_WORKSPACE_ROUTE}/`) ||
    pathname === "/workspace/team" ||
    pathname.startsWith("/workspace/team/") ||
    pathname === "/workspace/agents" ||
    pathname.startsWith("/workspace/agents/") ||
    pathname === "/workspace/intelligence" ||
    pathname.startsWith("/workspace/intelligence/") ||
    pathname === "/workspace/evolution" ||
    pathname.startsWith("/workspace/evolution/") ||
    pathname === "/workspace/knowledge" ||
    pathname.startsWith("/workspace/knowledge/") ||
    pathname === "/workspace/plugins" ||
    pathname.startsWith("/workspace/plugins/") ||
    pathname === "/workspace/skills" ||
    pathname.startsWith("/workspace/skills/")
  );
}

export const __testing = {
  isNavRouteActive,
};

type WorkspaceSurfaceMode = "agent" | "work" | "browser";

export function WorkspaceSurfaceSwitch({
  active,
  showWork = true,
}: {
  active: WorkspaceSurfaceMode;
  // OS appliance 剥离 company 时隐藏"工作"surface(PM 交给企业版插件)。
  showWork?: boolean;
}) {
  const { t } = useI18n();
  const items = [
    ...(showWork
      ? [
          {
            to: COMPANY_WORKSPACE_ROUTE,
            label: t.sidebar.navCompany,
            icon: BriefcaseIcon,
            active: active === "work",
            kind: "icon" as const,
          },
        ]
      : []),
    {
      to: PRIMARY_WORKSPACE_ROUTE,
      label: "Octopus",
      icon: BotIcon,
      active: active === "agent",
      kind: "brand" as const,
    },
    {
      to: "/browser",
      label: "浏览器",
      icon: GlobeIcon,
      active: active === "browser",
      kind: "icon" as const,
    },
  ];

  return (
    <div className="grid w-[150px] min-w-0 -translate-x-3 grid-cols-[30px_minmax(0,1fr)_30px] items-center gap-0.5 rounded-[14px] border border-border/45 bg-background/72 p-px shadow-[0_1px_2px_rgba(15,23,42,0.04),inset_0_1px_0_rgba(255,255,255,0.42)] group-data-[collapsible=icon]:w-auto group-data-[collapsible=icon]:translate-x-[-4px] group-data-[collapsible=icon]:grid-cols-1 group-data-[collapsible=icon]:border-0 group-data-[collapsible=icon]:bg-transparent group-data-[collapsible=icon]:p-0 group-data-[collapsible=icon]:shadow-none">
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <Link
            key={item.to}
            to={item.to}
            aria-current={item.active ? "page" : undefined}
            aria-label={item.label}
            className={cn(
              "min-w-0 rounded-xl transition-[background-color,color,box-shadow,opacity] duration-150",
              item.active
                ? "flex h-7 items-center justify-center bg-background text-foreground shadow-[0_1px_2px_rgba(15,23,42,0.055)] ring-1 ring-border/50 hover:bg-background/90"
                : "flex h-7 items-center justify-center text-muted-foreground hover:bg-background/55 hover:text-foreground",
              item.kind === "brand"
                ? "px-1 text-[12px] font-bold tracking-[0.005em]"
                : "px-0",
              "group-data-[collapsible=icon]:grid group-data-[collapsible=icon]:size-7 group-data-[collapsible=icon]:place-items-center group-data-[collapsible=icon]:px-0",
              item.active
                ? "group-data-[collapsible=icon]:flex"
                : "group-data-[collapsible=icon]:hidden",
              item.active && "group-data-[collapsible=icon]:bg-sidebar-accent",
            )}
          >
            <Icon
              className={cn(
                "size-3.5 shrink-0",
                item.kind === "brand" &&
                  "hidden group-data-[collapsible=icon]:block",
              )}
            />
            {item.kind === "brand" && (
              <span
                className={cn(
                  "min-w-0 truncate group-data-[collapsible=icon]:sr-only",
                )}
              >
                {item.label}
              </span>
            )}
          </Link>
        );
      })}
    </div>
  );
}

function SurfaceCreateButton({
  companySurfaceActive,
}: {
  companySurfaceActive: boolean;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const createAction = companySurfaceActive
    ? {
        label: t.sidebar.actionNewTask,
        icon: PlusIcon,
        to: "/workspace/team/new",
      }
    : {
        label: t.sidebar.actionNewChat,
        icon: MessageSquarePlusIcon,
        to: PRIMARY_WORKSPACE_ROUTE,
      };
  const CreateIcon = createAction.icon;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          title={createAction.label}
          aria-label={createAction.label}
          onClick={() => navigate(createAction.to)}
          className="flex h-7 w-full shrink-0 items-center justify-center gap-1.5 rounded-full border border-border/40 bg-background/48 px-3 text-[11px] font-medium text-muted-foreground shadow-[0_1px_2px_rgba(15,23,42,0.03)] transition-[background-color,border-color,color,box-shadow] hover:border-border hover:bg-background hover:text-foreground group-data-[collapsible=icon]:size-8 group-data-[collapsible=icon]:rounded-xl group-data-[collapsible=icon]:px-0"
        >
          <CreateIcon className="size-4" />
          <span className="group-data-[collapsible=icon]:sr-only">
            {createAction.label}
          </span>
        </button>
      </TooltipTrigger>
      <TooltipContent side="right">{createAction.label}</TooltipContent>
    </Tooltip>
  );
}

function NavRow({ item, pathname }: { item: NavItem; pathname: string }) {
  const active = isNavRouteActive(pathname, item.to);
  const Icon = item.icon;
  return (
    <SidebarMenuItem className="justify-center">
      <SidebarMenuButton
        asChild
        isActive={active}
        tooltip={item.label}
        className={cn(
          "group/nav relative h-9 w-full rounded-lg opacity-76 transition-[opacity,background-color,border-color] duration-150 text-[13px]",
          "border border-transparent hover:border-border/45 hover:bg-muted/32 hover:opacity-100",
          "data-[active=true]:opacity-100",
          "data-[active=true]:border-primary/14 data-[active=true]:bg-[color:color-mix(in_oklch,var(--sidebar-accent)_76%,transparent)]",
          "data-[active=true]:shadow-sm data-[active=true]:shadow-black/[0.025]",
          "data-[active=true]:before:absolute data-[active=true]:before:left-0 data-[active=true]:before:top-1.5 data-[active=true]:before:bottom-1.5 data-[active=true]:before:w-[2px] data-[active=true]:before:rounded-r data-[active=true]:before:bg-primary/75",
        )}
      >
        <Link
          to={item.to}
          aria-current={active ? "page" : undefined}
          className="flex items-center gap-2 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:gap-0"
        >
          <span
            className={cn(
              "flex size-6 shrink-0 items-center justify-center rounded-md transition-colors",
              active
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground group-hover/nav:text-foreground",
            )}
          >
            <Icon className="size-[16px]" />
          </span>
          <span className="group-data-[collapsible=icon]:hidden">
            {item.label}
          </span>
        </Link>
      </SidebarMenuButton>
    </SidebarMenuItem>
  );
}

/** WeChat-style avatar block for a thread row.
 *
 *   - 1 agent  → single round avatar
 *   - 2-4      → 2×2 grid
 *   - 5-9      → 3×3 grid
 *   - 0        → fallback initial (the FolderIcon's space — a soft gray dot
 *                with no avatar URL · solo team threads with no roster yet)
 *
 *  Each cell is the agent avatar fetched from
 *  ``/api/agents/{name}/avatar``. The img onError flips to a colored
 *  initial fallback so a missing avatar doesn't show a broken-image
 *  glyph. The whole block is rounded-md to mimic WeChat's group icon. */
function ThreadAvatar({
  agents,
  className,
}: {
  agents: string[];
  className?: string;
}) {
  const list = agents.slice(0, 9);
  const isGroup = list.length >= 2;
  // Decide the grid columns. WeChat uses 2-col for 2-4 members,
  // 3-col for 5-9. Single avatar gets a plain round image.
  const cols = list.length <= 1 ? 1 : list.length <= 4 ? 2 : 3;
  if (list.length === 0) {
    return (
      <span
        className={cn(
          "inline-block rounded-full bg-muted/60 flex-shrink-0",
          className,
        )}
      />
    );
  }
  if (!isGroup) {
    return (
      <AvatarCell
        agentId={list[0]!}
        className={cn("rounded-full overflow-hidden", className)}
      />
    );
  }
  return (
    <span
      className={cn(
        "grid bg-muted/30 rounded-md overflow-hidden flex-shrink-0",
        className,
      )}
      style={{
        gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
        // Tiny gap between cells like WeChat
        gap: "1px",
      }}
    >
      {list.map((id, i) => (
        <AvatarCell
          key={`${id}-${i}`}
          agentId={id}
          className="block w-full h-full"
        />
      ))}
    </span>
  );
}

/** One image cell · falls back to a colored initial circle if the
 *  backend has no avatar for the agent (404 on
 *  ``/api/agents/<id>/avatar``). The initial fallback uses a hash-based
 *  color so different agents don't all blend into the same grey. */
function AvatarCell({
  agentId,
  className,
}: {
  agentId: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <span
        className={cn(
          "flex items-center justify-center bg-muted text-[8px] font-semibold uppercase text-muted-foreground",
          className,
        )}
        title={agentId}
      >
        {(agentId[0] || "?").toUpperCase()}
      </span>
    );
  }
  return (
    <img
      src={withAgentAvatarVersion(
        `${getBackendBaseURL()}/api/agents/${encodeURIComponent(agentId)}/avatar`,
      )}
      alt={agentId}
      title={agentId}
      onError={() => setFailed(true)}
      className={cn("object-cover", className)}
    />
  );
}

function ProjectGroupIcon({ project }: { project: string }) {
  const isTeamProject = project === "Team" || project.startsWith("Team · ");
  const isCodeProject = project === "Code";

  if (!isTeamProject && !isCodeProject) {
    return <FolderIcon className="size-[18px] shrink-0 opacity-70" />;
  }

  const AccentIcon = isTeamProject ? NetworkIcon : Code2Icon;

  return (
    <span className="relative grid size-5 shrink-0 place-items-center text-muted-foreground/75">
      <FolderIcon className="size-[18px]" strokeWidth={1.9} />
      <span
        className={cn(
          "absolute -bottom-0.5 -right-0.5 grid size-3.5 place-items-center rounded-[4px] border border-sidebar-border bg-sidebar",
          isTeamProject
            ? "text-emerald-600 dark:text-emerald-400"
            : "text-sky-600 dark:text-sky-400",
        )}
      >
        <AccentIcon className="size-[9px]" strokeWidth={2.6} />
      </span>
    </span>
  );
}

function ConfirmDeleteThreadDialog({
  title,
  open,
  pending,
  onOpenChange,
  onConfirm,
}: {
  title: string;
  open: boolean;
  pending: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}) {
  const { t } = useI18n();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="w-[min(360px,calc(100vw-2rem))] gap-3 rounded-lg p-4 shadow-xl sm:max-w-[360px]"
      >
        <DialogHeader className="gap-1 text-left">
          <DialogTitle className="text-[15px]">
            {t.sidebar.deleteThreadTooltip}
          </DialogTitle>
          <DialogDescription className="text-[12.5px] leading-5">
            {t.sidebar.confirmDeleteThread(title)}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="mt-1 flex-row justify-end gap-2">
          <button
            type="button"
            disabled={pending}
            onClick={() => onOpenChange(false)}
            className="inline-flex h-8 items-center justify-center rounded-md border border-border bg-background px-3 text-[12.5px] font-medium text-foreground/80 transition-colors hover:bg-muted disabled:pointer-events-none disabled:opacity-60"
          >
            {t.common.cancel}
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={onConfirm}
            className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-destructive/25 bg-destructive/[0.07] px-3 text-[12.5px] font-medium text-destructive transition-colors hover:border-destructive/35 hover:bg-destructive/[0.11] disabled:pointer-events-none disabled:opacity-60"
          >
            {pending ? (
              <span className="size-3 animate-spin rounded-full border border-current border-t-transparent" />
            ) : (
              <Trash2Icon className="size-3.5" />
            )}
            {t.common.delete}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ProjectGroup({
  project,
  threads,
  pathname,
  deletable,
  deleting,
  onDeleteProject,
}: {
  project: string;
  threads: ThreadSummary[];
  pathname: string;
  deletable: boolean;
  deleting: boolean;
  onDeleteProject: (project: string) => void | Promise<void>;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(true);
  const [threadToDelete, setThreadToDelete] = useState<ThreadSummary | null>(
    null,
  );
  const deleteThread = useDeleteThread();
  const navigate = useNavigate();
  const deleteProject = () => {
    if (!window.confirm(t.sidebar.confirmDeleteProject(project))) {
      return;
    }
    void onDeleteProject(project);
  };
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <SidebarGroup className="p-0 px-2 py-1">
        <div className="group/project relative">
          <CollapsibleTrigger
            className={cn(
              "flex h-9 w-full items-center gap-2 rounded-md px-1 text-sm",
              deletable ? "pr-8" : "pr-1",
              "text-foreground/85 hover:text-foreground hover:bg-muted/40 transition-colors",
              "outline-none",
            )}
          >
            <ProjectGroupIcon project={project} />
            <span className="truncate">{project}</span>
            <span
              className={cn(
                "ml-auto text-xs text-muted-foreground/60 shrink-0 transition-opacity",
                deletable && "group-hover/project:opacity-0",
              )}
            >
              {threads.length}
            </span>
          </CollapsibleTrigger>
          {deletable && (
            <button
              type="button"
              title={t.sidebar.deleteProjectTooltip}
              disabled={deleting}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                deleteProject();
              }}
              className={cn(
                "absolute right-1 top-1/2 -translate-y-1/2 flex size-5 items-center justify-center rounded-md text-muted-foreground/60 opacity-0 transition-opacity duration-150 group-hover/project:opacity-100 hover:bg-destructive/10 hover:text-destructive",
                deleting && "opacity-100 cursor-wait hover:bg-transparent",
              )}
            >
              {deleting ? (
                <span className="size-3 animate-spin rounded-full border border-current border-t-transparent" />
              ) : (
                <Trash2Icon className="size-3" />
              )}
            </button>
          )}
        </div>
        <CollapsibleContent className="overflow-hidden">
          {/* Threads nested under the folder — indent so titles start
              beyond the 📁 icon, matching the Codex reference. */}
          <ul className="mt-0.5 space-y-px pl-6">
            {threads.slice(0, 12).map((thread) => {
              const active = pathname.includes(thread.id);
              return (
                <li key={thread.id} className="group/thread relative">
                  <Link
                    to={thread.href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex min-h-8 items-center gap-2 rounded-md py-1 pl-2 pr-7 text-[13px] opacity-75 transition-[opacity,background-color] duration-150",
                      "hover:opacity-100 hover:bg-muted/40",
                      active &&
                        "opacity-100 bg-[color:color-mix(in_oklch,var(--sidebar-accent)_55%,transparent)]",
                    )}
                  >
                    <ThreadAvatar
                      agents={thread.agents}
                      className="size-5 shrink-0"
                    />
                    <span className="min-w-0 flex-1 truncate leading-tight">
                      {thread.title}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground/60 transition-[opacity,color] group-hover/thread:opacity-0 group-hover/thread:text-muted-foreground/90">
                      {formatRelativeTimestamp(thread.updatedAt)}
                    </span>
                  </Link>
                  <button
                    type="button"
                    title={t.sidebar.deleteThreadTooltip}
                    disabled={deleteThread.isPending}
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      setThreadToDelete(thread);
                    }}
                    className="absolute right-1 top-1/2 flex size-5 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground/60 opacity-0 transition-opacity duration-150 hover:bg-destructive/10 hover:text-destructive group-hover/thread:opacity-100"
                  >
                    <Trash2Icon className="size-3" />
                  </button>
                </li>
              );
            })}
          </ul>
        </CollapsibleContent>
      </SidebarGroup>
      <ConfirmDeleteThreadDialog
        title={threadToDelete?.title ?? ""}
        open={threadToDelete !== null}
        pending={deleteThread.isPending}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) setThreadToDelete(null);
        }}
        onConfirm={() => {
          if (!threadToDelete) return;
          const target = threadToDelete;
          setThreadToDelete(null);
          deleteThread.mutate({ threadId: target.id });
          if (pathname === target.href) {
            void navigate(PRIMARY_WORKSPACE_ROUTE);
          }
        }}
      />
    </Collapsible>
  );
}

interface SectionAction {
  icon: LucideIcon;
  label: string;
  active?: boolean;
  onClick?: () => void;
  href?: string;
}

/* Implementation note. */
function SectionHeader({
  label,
  actions,
  open,
  onToggleOpen,
}: {
  label: string;
  actions?: SectionAction[];
  /** If provided, the header becomes a collapse trigger (chevron ▶/▼). */
  open?: boolean;
  onToggleOpen?: () => void;
}) {
  const hasToggle =
    typeof open === "boolean" && typeof onToggleOpen === "function";
  return (
    <div className="group/section flex h-9 items-center justify-between gap-2 px-1">
      {hasToggle ? (
        <button
          type="button"
          onClick={onToggleOpen}
          className="flex min-w-0 flex-1 items-center gap-1 rounded-md text-left text-sm font-medium text-muted-foreground hover:text-foreground transition-colors outline-none"
        >
          {/* Implementation note. */}
          <ChevronRightIcon
            className={cn(
              "size-3 shrink-0 transition-transform duration-150",
              open && "rotate-90",
            )}
          />
          <span className="truncate">{label}</span>
        </button>
      ) : (
        <span className="text-sm font-medium text-muted-foreground">
          {label}
        </span>
      )}
      {actions && actions.length > 0 && (
        <div className="flex items-center gap-0.5">
          {actions.map((a) => {
            const Icon = a.icon;
            const cls = cn(
              "flex size-6 items-center justify-center rounded-md text-muted-foreground/70 hover:bg-muted hover:text-foreground transition-colors",
              a.active && "bg-muted text-foreground",
            );
            if (a.href) {
              return (
                <Link key={a.label} to={a.href} title={a.label} className={cls}>
                  <Icon className="size-3.5" />
                </Link>
              );
            }
            return (
              <button
                key={a.label}
                type="button"
                title={a.label}
                onClick={(e) => {
                  e.stopPropagation();
                  a.onClick?.();
                }}
                className={cls}
              >
                <Icon className="size-3.5" />
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ProjectsSection({
  groups,
  byProject,
  pathname,
  draftOpen,
  deletableProjects,
  deletingProject,
  groupingEnabled,
  onDraftCommit,
  onDraftCancel,
  onDeleteProject,
  onToggleGrouping,
}: {
  groups: string[];
  byProject: Record<string, ThreadSummary[]>;
  pathname: string;
  draftOpen: boolean;
  deletableProjects: Set<string>;
  deletingProject: string | null;
  groupingEnabled: boolean;
  onDraftCommit: (name: string) => void;
  onDraftCancel: () => void;
  onDeleteProject: (project: string) => void | Promise<void>;
  onToggleGrouping: () => void;
}) {
  const { t } = useI18n();
  // Persist the open/closed state in localStorage so it stays
  // consistent across routes (chat ↔ code ↔ team) and across page
  // reloads. Without this, useState resets on remount and users
  // Implementation note.
  // expected the section to behave the same everywhere.
  const [open, setOpen] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    const v = window.localStorage.getItem("octopus.sidebar.projects-open");
    return v === null ? true : v === "1";
  });
  useEffect(() => {
    try {
      window.localStorage.setItem(
        "octopus.sidebar.projects-open",
        open ? "1" : "0",
      );
    } catch (e) {
      swallow(e, "storage");
    }
  }, [open]);
  return (
    <div className="mt-2 group-data-[collapsible=icon]:hidden">
      <SidebarGroup className="p-0 px-2 pb-0">
        <SectionHeader
          label={t.sidebar.sectionProjects}
          open={groupingEnabled && open}
          onToggleOpen={() => {
            if (!groupingEnabled) {
              onToggleGrouping();
              return;
            }
            setOpen((v) => !v);
          }}
          actions={[
            {
              icon: FolderIcon,
              label: groupingEnabled
                ? t.sidebar.actionDisableProjectGrouping
                : t.sidebar.actionEnableProjectGrouping,
              active: groupingEnabled,
              onClick: onToggleGrouping,
            },
            ...(groupingEnabled
              ? [
                  {
                    icon: ArrowUpDownIcon,
                    label: t.sidebar.actionSort,
                    onClick: () => eventBus.emit("projects:sort"),
                  },
                  {
                    icon: FolderPlusIcon,
                    label: t.sidebar.actionNewProject,
                    onClick: () =>
                      window.dispatchEvent(new Event("octopus:project-new")),
                  },
                ]
              : []),
          ]}
        />
        {groupingEnabled && open && (
          <div className="mt-0.5">
            {draftOpen && (
              <ProjectDraftRow
                onCommit={onDraftCommit}
                onCancel={onDraftCancel}
              />
            )}
            {groups.map((project) => (
              <ProjectGroup
                key={project}
                project={project}
                threads={byProject[project]!}
                pathname={pathname}
                deletable={deletableProjects.has(project)}
                deleting={deletingProject === project}
                onDeleteProject={onDeleteProject}
              />
            ))}
          </div>
        )}
      </SidebarGroup>
    </div>
  );
}

function ProjectDraftRow({
  onCommit,
  onCancel,
}: {
  onCommit: (name: string) => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const [value, setValue] = useState("");
  return (
    <div className="flex h-8 items-center gap-2 rounded-md px-1">
      <FolderIcon className="size-4 shrink-0 opacity-70" />
      <input
        autoFocus
        value={value}
        placeholder={t.sidebar.projectNamePlaceholder}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            onCommit(value);
          } else if (e.key === "Escape") {
            e.preventDefault();
            onCancel();
          }
        }}
        onBlur={() => {
          // Commit on blur if the user typed something, otherwise cancel
          // so clicking elsewhere doesn't leave a ghost draft row.
          if (value.trim()) onCommit(value);
          else onCancel();
        }}
        className={cn(
          "min-w-0 flex-1 rounded-md border border-primary/40 bg-background px-1.5 py-0.5",
          "text-[13px] outline-none focus:ring-1 focus:ring-primary/40",
        )}
      />
    </div>
  );
}

function ChatsSection({
  threads,
  pathname,
  label,
}: {
  threads: ThreadSummary[];
  pathname: string;
  label?: string;
}) {
  const { t: tr } = useI18n();
  // Match the ProjectsSection pattern · persist open/close so the
  // chats list doesn't appear surprisingly empty after a route hop
  // collapsed it.
  const [open, setOpen] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    const v = window.localStorage.getItem("octopus.sidebar.chats-open");
    return v === null ? true : v === "1";
  });
  useEffect(() => {
    try {
      window.localStorage.setItem(
        "octopus.sidebar.chats-open",
        open ? "1" : "0",
      );
    } catch (e) {
      swallow(e);
    }
  }, [open]);
  const deleteThread = useDeleteThread();
  const navigate = useNavigate();
  // Navigate to a fresh thread URL each click — using Link to /chats/new
  // when already on /chats/new is a no-op (same pathname → react-router
  // doesn't re-mount, so threadId never resets).
  const [threadToDelete, setThreadToDelete] = useState<ThreadSummary | null>(
    null,
  );
  const startNewChat = useCallback(() => {
    navigate(`/workspace/realtime/${uuid()}`);
  }, [navigate]);
  return (
    <div className="mt-4 group-data-[collapsible=icon]:hidden">
      <SidebarGroup className="p-0 px-2 pb-1">
        <SectionHeader
          label={label ?? tr.sidebar.sectionChats}
          open={open}
          onToggleOpen={() => setOpen((v) => !v)}
          actions={[
            {
              icon: ArrowUpDownIcon,
              label: tr.sidebar.actionSort,
              onClick: () => eventBus.emit("chats:sort"),
            },
            {
              icon: MessageSquarePlusIcon,
              label: tr.sidebar.actionNewChat,
              onClick: startNewChat,
            },
          ]}
        />
        {open &&
          (threads.length === 0 ? (
            <EmptyHint>{tr.sidebar.noChatsYet}</EmptyHint>
          ) : (
            <ul className="mt-0.5 space-y-px">
              {threads.slice(0, 20).map((t) => {
                const active = pathname.includes(t.id);
                return (
                  <li key={t.id} className="group/thread relative">
                    <Link
                      to={t.href}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "flex min-h-7 items-center gap-2 rounded-md pl-2 pr-7 py-1 text-xs opacity-75 transition-[opacity,background-color] duration-150",
                        "hover:opacity-100 hover:bg-muted/40",
                        active &&
                          "opacity-100 bg-[color:color-mix(in_oklch,var(--sidebar-accent)_55%,transparent)]",
                      )}
                    >
                      <span className="min-w-0 flex-1 truncate leading-tight">
                        {t.title}
                      </span>
                      <span className="shrink-0 text-[10px] text-muted-foreground/60 group-hover/thread:text-muted-foreground/90 group-hover/thread:opacity-0 transition-[opacity,color]">
                        {formatRelativeTimestamp(t.updatedAt)}
                      </span>
                    </Link>
                    <button
                      type="button"
                      title={tr.sidebar.deleteThreadTooltip}
                      disabled={deleteThread.isPending}
                      onClick={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        setThreadToDelete(t);
                      }}
                      className="absolute right-1 top-1/2 -translate-y-1/2 flex size-5 items-center justify-center rounded-md text-muted-foreground/60 opacity-0 transition-opacity duration-150 group-hover/thread:opacity-100 hover:bg-destructive/10 hover:text-destructive"
                    >
                      <Trash2Icon className="size-3" />
                    </button>
                  </li>
                );
              })}
            </ul>
          ))}
      </SidebarGroup>
      <ConfirmDeleteThreadDialog
        title={threadToDelete?.title ?? ""}
        open={threadToDelete !== null}
        pending={deleteThread.isPending}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) setThreadToDelete(null);
        }}
        onConfirm={() => {
          if (!threadToDelete) return;
          const target = threadToDelete;
          setThreadToDelete(null);
          deleteThread.mutate({ threadId: target.id });
          if (pathname === target.href) {
            void navigate(PRIMARY_WORKSPACE_ROUTE);
          }
        }}
      />
    </div>
  );
}

/* Implementation note. */
export function ModeSwitcher({
  active,
  layout = "sidebar",
}: {
  active: "workspace" | "browser";
  layout?: "sidebar" | "browser";
}) {
  const noDrag = { WebkitAppRegion: "no-drag" } as React.CSSProperties;

  const current =
    active === "workspace"
      ? { id: "workspace" as const, label: "Octopus" }
      : { id: "browser" as const, label: "\u6d4f\u89c8\u5668" };
  const target =
    layout === "sidebar" && active === "workspace"
      ? { href: PRIMARY_WORKSPACE_ROUTE, label: current.label }
      : active === "workspace"
        ? { href: "/browser", label: "\u6d4f\u89c8\u5668" }
        : { href: PRIMARY_WORKSPACE_ROUTE, label: "Octopus" };
  const title =
    layout === "sidebar" && active === "workspace"
      ? current.label
      : `切换至 ${target.label}`;
  const CurrentIcon = current.id === "workspace" ? OctopusMark : GlobeIcon;
  const currentIconSize = current.id === "workspace" ? "size-[18px]" : "size-4";

  return (
    <div
      className={cn(
        "min-w-0 flex flex-1 items-center gap-1",
        layout === "sidebar"
          ? "justify-start group-data-[collapsible=icon]:flex-none group-data-[collapsible=icon]:flex-col group-data-[collapsible=icon]:gap-0.5 group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:items-center"
          : // Implementation note.
            "px-1",
      )}
    >
      <Link
        to={target.href}
        title={title}
        style={noDrag}
        className={cn(
          "group/mode relative flex h-10 min-w-0 flex-1 shrink items-center justify-start gap-2.5 rounded-xl border border-border/55 bg-background/72 px-2.5 text-[15px] font-bold tracking-[0.01em] text-foreground shadow-[0_1px_2px_rgba(15,23,42,0.04),inset_0_1px_0_rgba(255,255,255,0.42)] transition-[background-color,border-color,box-shadow] hover:border-border hover:bg-background",
          "before:absolute before:bottom-[-5px] before:left-1/2 before:hidden before:size-2 before:-translate-x-1/2 before:rotate-45 before:bg-popover group-hover/mode:before:block",
          current.id === "workspace" ? "max-w-none" : "max-w-[142px]",
          layout === "sidebar" &&
            "group-data-[collapsible=icon]:size-9 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:p-0 group-data-[collapsible=icon]:before:hidden",
        )}
      >
        {current.id === "browser" ? (
          <span
            className={cn(
              "min-w-0 flex-1 truncate px-1.5 text-center",
              layout === "sidebar" && "group-data-[collapsible=icon]:hidden",
            )}
          >
            {current.label}
          </span>
        ) : null}
        <span
          className={cn(
            "flex size-8 shrink-0 items-center justify-center rounded-lg border border-border/60 bg-muted/45 text-foreground shadow-[0_1px_2px_rgba(15,23,42,0.08)] transition-transform group-hover/mode:scale-[1.03]",
            layout === "sidebar" && "group-data-[collapsible=icon]:size-9",
          )}
          aria-hidden
        >
          <CurrentIcon className={currentIconSize} />
        </span>
        {current.id === "workspace" ? (
          <span
            className={cn(
              "min-w-0 flex-1 truncate text-left text-[15px]",
              layout === "sidebar" && "group-data-[collapsible=icon]:hidden",
            )}
          >
            {current.label}
          </span>
        ) : null}
      </Link>
    </div>
  );
}

export function CollapseToggle({ compact = false }: { compact?: boolean }) {
  const { open, toggleSidebar, state } = useSidebar();
  const { t } = useI18n();
  const Icon = open ? PanelLeftCloseIcon : PanelLeftOpenIcon;
  const isCollapsed = state === "collapsed";

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={toggleSidebar}
          className={cn(
            "flex shrink-0 items-center justify-center justify-self-center border border-border/45 bg-background/72 text-muted-foreground shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-[background-color,border-color,color] hover:border-border/60 hover:bg-background hover:text-foreground",
            compact ? "size-6 rounded-lg" : "size-10 rounded-xl",
          )}
        >
          <Icon className={compact ? "size-3.5" : "size-4"} />
        </button>
      </TooltipTrigger>
      <TooltipContent side={isCollapsed ? "right" : "bottom"} align="center">
        {open ? t.sidebar.collapseSidebar : t.sidebar.expandSidebar}
      </TooltipContent>
    </Tooltip>
  );
}

function EmptyHint({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-1 rounded-md border border-dashed border-border/50 px-2.5 py-1.5 text-[11px] leading-tight text-muted-foreground/75">
      {children}
    </div>
  );
}

/** Compact robot mark tuned for the small sidebar tile. */
function OctopusMark({ className }: { className?: string }) {
  return <BotIcon className={className} aria-hidden="true" />;
}
