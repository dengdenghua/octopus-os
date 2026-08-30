/* Implementation note. */

import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  AlertCircleIcon,
  BookOpenIcon,
  BotIcon,
  CompassIcon,
  FolderKanbanIcon,
  type LayoutGridIcon,
  ChevronDownIcon,
  CloudDownloadIcon,
  DnaIcon,
  CirclePauseIcon,
  Loader2Icon,
  MoreHorizontalIcon,
  PanelLeftIcon,
  PlusIcon,
  PuzzleIcon,
  PaletteIcon,
  RadioIcon,
  SearchIcon,
  StoreIcon,
  TrendingUpIcon,
  Trash2Icon,
  PowerIcon,
  ArrowRightIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  SparklesIcon,
} from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogClose,
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
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SidebarTrigger } from "@/components/ui/sidebar";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ACTIVE_AGENT_KEY, useActiveAgentId } from "@/core/agents/active";
import { emitAgentChanged } from "@/core/events";
import { taskWorkspaceRoute } from "@/core/router/task-workspace-route";
import {
  taskCollaboratorRouteForLeader,
  writeTaskCollaboratorPreset,
} from "@/core/collaboration/task-collaborator-preset";
import { swallow } from "@/core/utils/log";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import {
  fetchRuntimePluginStatus,
  installAgent,
  installCloudPlugin,
  rollbackCloudPlugin,
  setCloudPluginEnabled,
  uninstallCloudPlugin,
  type RuntimePluginStatus,
} from "@/core/agents/agent-world-api";
import { listAgents as listLocalAgents } from "@/core/agents/api";
import { waitForBackendAvailability } from "@/core/backend/readiness";
import type { AgentWorldAgent } from "@/core/agents/types";
import {
  DEFAULT_PRIMARY_AGENT_ID,
  isPrimaryPersonaAgentId,
} from "@/core/agents/persona-policy";
import {
  setModuleEnabled,
  setModuleAvailable,
  useEnabledModuleIds,
} from "@/core/modules/enabled-modules";
import {
  loadWorkbenchAvailabilitySnapshot,
  syncWorkbenchAvailability,
} from "@/core/workbench/availability";
import {
  WORKBENCH_BUILTIN_APPS,
  type WorkbenchBuiltinApp,
  type WorkbenchBuiltinIcon,
} from "@/core/workbench/apps";

import { AgentCard } from "./agent-card";
import { AgentWorldCard } from "./agent-world-card";
import { CapabilityMarketPanel } from "@/components/store/capability-market-panel";
import { DEFAULT_FEATURED_APP_IDS } from "@/components/store/app-marketplace-panel";
import { CloudSkillsPanel } from "@/components/store/cloud-skills-panel";
import { WorkBuddyCloudStorePanel } from "@/components/store/workbuddy-cloud-store-panel";
import { SmartTeamDialog } from "./smart-team-dialog";

const AgentRoleProfileDialog = lazy(() =>
  import("./agent-role-profile-dialog").then((module) => ({
    default: module.AgentRoleProfileDialog,
  })),
);

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

// Types + data + helpers extracted to agent-world-data.ts
import {
  AGENT_CATEGORY_FILTERS,
  LOCAL_AGENT_IDS,
  LOCAL_AGENT_RANK,
  localAgentToWorldAgent,
  worldAgentToAgent,
  type AgentCategoryFilter,
} from "./agent-world-data";

const ECHO_CHARACTER_DISPLAY_NAMES = new Set([
  "eve",
  "kane",
  "leon",
  "luna",
  "mira voss",
  "noah",
  "raven",
  "shion",
  "zero",
]);

const BUILTIN_APP_ICONS = {
  projects: FolderKanbanIcon,
  trading: TrendingUpIcon,
  design: PaletteIcon,
  narrative: BookOpenIcon,
  evolution: DnaIcon,
  intelligence: RadioIcon,
  community: CompassIcon,
} satisfies Record<WorkbenchBuiltinIcon, typeof LayoutGridIcon>;

const BUILTIN_APP_ICON_STYLES = {
  projects: "bg-blue-500/10 text-blue-600 ring-blue-500/15 dark:text-blue-400",
  trading:
    "bg-emerald-500/10 text-emerald-600 ring-emerald-500/15 dark:text-emerald-400",
  design:
    "bg-violet-500/10 text-violet-600 ring-violet-500/15 dark:text-violet-400",
  narrative:
    "bg-fuchsia-500/10 text-fuchsia-600 ring-fuchsia-500/15 dark:text-fuchsia-400",
  evolution:
    "bg-violet-500/10 text-violet-600 ring-violet-500/15 dark:text-violet-400",
  intelligence: "bg-sky-500/10 text-sky-600 ring-sky-500/15 dark:text-sky-400",
  community:
    "bg-amber-500/10 text-amber-600 ring-amber-500/15 dark:text-amber-400",
} satisfies Record<WorkbenchBuiltinIcon, string>;

function normalizeAgentNameKey(value: string): string {
  return value
    .toLowerCase()
    .replace(/[_-]+/g, " ")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim()
    .replace(/\s+/g, " ");
}

export function agentWorldIdentityKey(agent: AgentWorldAgent): string {
  const profile = agent.character_profile as
    | { name?: unknown; codename?: unknown }
    | null
    | undefined;
  const profileName =
    typeof profile?.name === "string"
      ? normalizeAgentNameKey(profile.name)
      : "";
  if (profileName) return profileName;

  const displayName = agent.display_name || agent.name || agent.id;
  const slashBaseName = displayName.split(/\s*\/\s*/)[0] ?? "";
  const slashBaseKey = normalizeAgentNameKey(slashBaseName);
  if (
    displayName.includes("/") &&
    ECHO_CHARACTER_DISPLAY_NAMES.has(slashBaseKey)
  ) {
    return slashBaseKey;
  }

  return normalizeAgentNameKey(displayName);
}

function scoreAgentForDisplay(agent: AgentWorldAgent): number {
  let score = 0;
  if (agent.is_installed) score += 1_000_000_000;
  if (LOCAL_AGENT_IDS.has(agent.id)) score += 100_000_000;
  if (agent.is_official) score += 10_000_000;
  if (agent.is_featured) score += 1_000_000;
  score += Math.max(0, agent.downloads ?? 0);
  score += Math.max(0, agent.rating_count ?? 0) * 10;
  score += Math.round(Math.max(0, agent.rating ?? 0) * 100);
  return score;
}

/**
 * Resolve a `?agent=` HUD target to the row the HUD actually renders.
 *
 * The bottom-left switcher and the HUD dedupe by different rules, so an exact
 * name match is not enough: the switcher's Noah row is `market_researcher`,
 * while the HUD collapses every Noah into `echo_noah`. Matching on name alone
 * missed and the HUD silently opened on an arbitrary role. So fall back to the
 * shared identity key — look the requested name up in the full list, then find
 * whichever agent survived dedupe under the same identity.
 */
export function resolveHudAgent(
  all: AgentWorldAgent[],
  deduped: AgentWorldAgent[],
  requestedName: string,
): AgentWorldAgent | null {
  const wanted = requestedName.trim();
  if (!wanted) return null;

  const exact = deduped.find((a) => a.name === wanted || a.id === wanted);
  if (exact) return exact;

  const raw = all.find((a) => a.name === wanted || a.id === wanted);
  if (!raw) return null;
  const key = agentWorldIdentityKey(raw);
  if (!key) return null;
  return deduped.find((a) => agentWorldIdentityKey(a) === key) ?? null;
}

export function dedupeAgentWorldAgents(
  agents: AgentWorldAgent[],
): AgentWorldAgent[] {
  const byName = new Map<string, AgentWorldAgent>();
  for (const agent of agents) {
    const key = agentWorldIdentityKey(agent);
    if (!key) continue;
    const current = byName.get(key);
    if (
      !current ||
      scoreAgentForDisplay(agent) > scoreAgentForDisplay(current)
    ) {
      byName.set(key, agent);
    }
  }
  return Array.from(byName.values());
}

export function AgentsTab({
  agents,
  filteredAgents,
  loading,
  loadError,
  activeCategory,
  onCategoryChange,
  onSelectAgent,
  onInstallChange,
  onRetry,
  onCreateAgent,
  showManagementActions = true,
  sceneOnly = false,
}: {
  agents: AgentWorldAgent[];
  filteredAgents: AgentWorldAgent[];
  loading: boolean;
  loadError: boolean;
  activeCategory: AgentCategoryFilter;
  onCategoryChange: (category: AgentCategoryFilter) => void;
  onSelectAgent: (agent: AgentWorldAgent) => void;
  onInstallChange: () => void;
  onRetry: () => void;
  onCreateAgent: () => void;
  showManagementActions?: boolean;
  sceneOnly?: boolean;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [installingAll, setInstallingAll] = useState(false);
  const [confirmInstallAll, setConfirmInstallAll] = useState(false);
  const [smartTeamOpen, setSmartTeamOpen] = useState(false);
  const visibleAgents = useMemo(
    () =>
      filteredAgents.slice().sort((a, b) => {
        if (a.is_installed !== b.is_installed) {
          return a.is_installed ? -1 : 1;
        }
        const rankA = LOCAL_AGENT_RANK.get(a.id) ?? Number.MAX_SAFE_INTEGER;
        const rankB = LOCAL_AGENT_RANK.get(b.id) ?? Number.MAX_SAFE_INTEGER;
        if (rankA !== rankB) return rankA - rankB;
        return a.display_name.localeCompare(b.display_name);
      }),
    [filteredAgents],
  );
  const installedCount = useMemo(
    () => agents.filter((agent) => agent.is_installed).length,
    [agents],
  );
  const installableAgents = useMemo(
    () => visibleAgents.filter((agent) => !agent.is_installed),
    [visibleAgents],
  );
  const installableCount = agents.length - installedCount;
  const featuredScenarios = useMemo(
    () =>
      [
        {
          id: "white-ghost",
          title: "白幽灵行动组",
          description: "完整主角团协同，适合复杂任务、跨工具执行与现场决策。",
          memberIds: [
            "general",
            "coder",
            "desktop_operator",
            "vibe_selling",
            "ecommerce_mind",
            "market_researcher",
            "aoi",
          ],
          domains: ["general", "automation"],
          accent:
            "from-violet-100/90 via-fuchsia-50/70 to-background dark:from-violet-950/55 dark:via-fuchsia-950/20",
        },
        {
          id: "product-lab",
          title: "产品研发冲刺",
          description: "从需求拆解、架构实现到桌面验收，组成一支小型交付团队。",
          memberIds: ["coder", "desktop_operator", "general"],
          domains: ["coding", "automation"],
          accent:
            "from-sky-100/90 via-cyan-50/70 to-background dark:from-sky-950/55 dark:via-cyan-950/20",
        },
        {
          id: "investment-room",
          title: "投研决策室",
          description:
            "聚合市场研究、信息验证与商业判断，形成可执行的投资结论。",
          memberIds: ["market_researcher", "general", "ecommerce_mind"],
          domains: ["research", "finance"],
          accent:
            "from-emerald-100/90 via-teal-50/70 to-background dark:from-emerald-950/55 dark:via-teal-950/20",
        },
        {
          id: "growth-studio",
          title: "品牌增长工作室",
          description: "把用户洞察、内容创意与商业转化串成一条完整增长链路。",
          memberIds: ["vibe_selling", "general", "ecommerce_mind"],
          domains: ["creative", "ecommerce"],
          accent:
            "from-amber-100/90 via-orange-50/70 to-background dark:from-amber-950/55 dark:via-orange-950/20",
        },
        {
          id: "automation-cell",
          title: "自动化执行中枢",
          description: "代码、桌面和流程三线并行，适合批量操作与长链路任务。",
          memberIds: ["desktop_operator", "coder", "aoi"],
          domains: ["automation", "coding"],
          accent:
            "from-slate-200/90 via-blue-50/60 to-background dark:from-slate-800/80 dark:via-blue-950/20",
        },
      ]
        .filter(
          (scenario) =>
            activeCategory === "all" ||
            scenario.domains.includes(activeCategory),
        )
        .map((scenario) => ({
          ...scenario,
          members: scenario.memberIds
            .map((id) =>
              agents.find((agent) => agent.id === id || agent.name === id),
            )
            .filter((agent): agent is AgentWorldAgent => Boolean(agent)),
        }))
        .filter((scenario) => scenario.members.length > 0),
    [activeCategory, agents],
  );

  const launchScenario = (scenario: (typeof featuredScenarios)[number]) => {
    const [leader, ...collaborators] = scenario.members;
    if (!leader) return;
    writeTaskCollaboratorPreset({
      leaderId: leader.name,
      collaboratorIds: collaborators.map((agent) => agent.name),
      mode: "cluster",
      label: scenario.title,
      openPicker: false,
    });
    navigate(taskCollaboratorRouteForLeader(leader.name));
  };

  useEffect(() => {
    setConfirmInstallAll(false);
  }, [activeCategory, installableAgents.length]);

  const handleInstallAll = async () => {
    if (installingAll || installableAgents.length === 0) return;
    if (!confirmInstallAll) {
      setConfirmInstallAll(true);
      return;
    }
    setInstallingAll(true);
    let installed = 0;
    let failed = 0;
    for (const agent of installableAgents) {
      try {
        await installAgent(agent.id);
        installed += 1;
      } catch (error) {
        failed += 1;
        swallow(error);
      }
    }
    setInstallingAll(false);
    setConfirmInstallAll(false);
    onInstallChange();
    if (installed > 0) {
      toast.success(
        failed > 0
          ? t.agentWorldUnified.installSuccessWithFailure(installed, failed)
          : t.agentWorldUnified.installSuccess(installed),
      );
    } else if (failed > 0) {
      toast.error(t.agentWorldUnified.installFailed);
    }
  };

  if (loading) {
    if (sceneOnly) {
      return (
        <section aria-label="正在加载精选场景" className="space-y-2">
          <Skeleton className="h-4 w-20" />
          <div className="flex gap-3 overflow-hidden">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton
                key={i}
                className="h-44 w-[280px] shrink-0 rounded-2xl"
              />
            ))}
          </div>
        </section>
      );
    }
    return (
      <div
        data-testid="agents-loading-skeleton"
        className="space-y-3"
        role="status"
        aria-live="polite"
      >
        <span className="sr-only">{t.agentWorldUnified.loadingAgents}</span>
        <Skeleton className="h-8 w-full" />
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-32" />
          ))}
        </div>
      </div>
    );
  }

  if (loadError && agents.length === 0) {
    if (sceneOnly) {
      return (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 rounded-lg border border-border-subtle bg-muted/20 px-3 py-2"
        >
          <span className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
            <AlertCircleIcon className="size-3.5 shrink-0" aria-hidden="true" />
            精选场景暂时不可用
          </span>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 shrink-0 px-2 text-xs"
            onClick={onRetry}
          >
            {t.agentWorldUnified.retryAgents}
          </Button>
        </div>
      );
    }
    return (
      <section
        role="alert"
        className="flex min-h-[300px] flex-col items-center justify-center rounded-xl border border-border-subtle bg-gradient-to-b from-muted/20 to-background px-5 py-8 text-center sm:min-h-[360px] sm:px-6 sm:py-10"
      >
        <span className="flex size-10 items-center justify-center rounded-xl border border-destructive/20 bg-destructive/5 text-destructive sm:size-12">
          <AlertCircleIcon className="size-5" aria-hidden="true" />
        </span>
        <h2 className="mt-3 max-w-sm text-base font-semibold text-foreground sm:mt-4">
          {t.agentWorldUnified.loadAgentsFailed}
        </h2>
        <div
          className={cn(
            "mt-4 grid w-full max-w-sm gap-2 sm:mt-5 sm:flex sm:w-auto sm:max-w-none sm:flex-wrap sm:items-center sm:justify-center",
            showManagementActions ? "grid-cols-2" : "grid-cols-1",
          )}
        >
          <Button
            type="button"
            className="min-w-0 px-2 text-xs sm:px-4 sm:text-sm"
            onClick={onRetry}
          >
            {t.agentWorldUnified.retryAgents}
          </Button>
          {showManagementActions ? (
            <>
              <Button
                type="button"
                className="min-w-0 px-2 text-xs sm:px-4 sm:text-sm"
                variant="outline"
                onClick={onCreateAgent}
              >
                <PlusIcon className="mr-1.5 hidden size-4 sm:block" />
                {t.agentWorld.newAgent}
              </Button>
            </>
          ) : null}
        </div>
      </section>
    );
  }

  return (
    <div className="space-y-3">
      {loadError && (
        <div
          role="alert"
          className="flex flex-col items-start justify-between gap-3 rounded-lg border border-destructive/25 bg-destructive/5 px-3 py-3 text-sm md:flex-row md:items-center"
        >
          <span className="flex items-center gap-2 text-destructive">
            <AlertCircleIcon className="size-4 shrink-0" aria-hidden="true" />
            {t.agentWorldUnified.loadAgentsFailed}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="w-full sm:w-auto"
            onClick={onRetry}
          >
            {t.agentWorldUnified.retryAgents}
          </Button>
        </div>
      )}
      {!sceneOnly ? (
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <div className="min-w-0 flex-1">
            <div
              data-testid="agents-category-scroll"
              className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1 pr-1 [scrollbar-width:none] [-webkit-overflow-scrolling:touch] [&::-webkit-scrollbar]:hidden"
              role="group"
              aria-label={t.agentWorldUnified.domainFilterLabel}
            >
              {AGENT_CATEGORY_FILTERS.map((category) => {
                const label = t.agentWorldUnified.domains[category];
                return (
                  <Button
                    key={category}
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => onCategoryChange(category)}
                    aria-pressed={activeCategory === category}
                    className={cn(
                      "h-8 shrink-0 rounded-md px-3 text-xs font-normal text-muted-foreground shadow-none",
                      activeCategory === category &&
                        "bg-muted font-medium text-foreground",
                    )}
                  >
                    {label}
                  </Button>
                );
              })}
            </div>
          </div>

          {showManagementActions ? (
            <div className="flex shrink-0 flex-wrap items-center gap-1.5 text-xs text-muted-foreground md:justify-end">
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 rounded-lg border-violet-500/25 bg-violet-500/5 px-2.5 text-xs font-medium text-violet-700 shadow-none hover:bg-violet-500/10 dark:text-violet-300"
                onClick={() => setSmartTeamOpen(true)}
              >
                <SparklesIcon className="mr-1.5 size-3.5" />
                智能组队
              </Button>
              <span className="inline-flex h-8 items-center rounded-lg border border-border bg-background px-2.5">
                <span className="text-muted-foreground/80">
                  {t.agentWorldUnified.installedLabel}
                </span>
                <span className="ml-1 font-medium text-foreground">
                  {installedCount}
                </span>
              </span>
              <span className="inline-flex h-8 items-center rounded-lg border border-border bg-background px-2.5">
                <span className="text-muted-foreground/80">
                  {t.agentWorldUnified.installableLabel}
                </span>
                <span className="ml-1 font-medium text-foreground">
                  {Math.max(0, installableCount)}
                </span>
              </span>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-8 rounded-lg border border-border bg-background px-2.5 text-xs font-medium text-muted-foreground shadow-none hover:bg-muted/45 hover:text-foreground"
                disabled={installingAll || installableAgents.length === 0}
                onClick={() => void handleInstallAll()}
                title={
                  confirmInstallAll
                    ? t.agentWorldUnified.installAllConfirmTitle(
                        installableAgents.length,
                      )
                    : t.agentWorldUnified.installAllConfirmHint
                }
              >
                {installingAll && (
                  <Loader2Icon className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                )}
                {confirmInstallAll
                  ? t.agentWorldUnified.installAllConfirmButton(
                      installableAgents.length,
                    )
                  : t.agentWorldUnified.installAllButton}
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    size="sm"
                    className="h-8 rounded-lg px-2.5 shadow-none"
                  >
                    <PlusIcon className="mr-1.5 h-3.5 w-3.5" />
                    {t.agentWorldUnified.addAgentButton}
                    <ChevronDownIcon className="ml-1 h-3.5 w-3.5" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-44">
                  <DropdownMenuItem onSelect={onCreateAgent}>
                    <PlusIcon className="h-4 w-4" />
                    {t.agentWorld.newAgent}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          ) : null}
        </div>
      ) : null}

      <SmartTeamDialog
        open={smartTeamOpen}
        onOpenChange={setSmartTeamOpen}
        agents={agents}
        onInstallChange={onInstallChange}
      />

      {featuredScenarios.length > 0 ? (
        <section aria-labelledby="featured-scenarios-title" className="pt-1">
          <div className="mb-2 flex items-center justify-between">
            <h3
              id="featured-scenarios-title"
              className="text-sm font-semibold text-foreground"
            >
              精选场景
            </h3>
            <span className="text-[11px] text-muted-foreground">
              选择即组队
            </span>
          </div>
          <div className="-mx-1 flex gap-3 overflow-x-auto px-1 pb-2 [scrollbar-width:none] [-webkit-overflow-scrolling:touch] [&::-webkit-scrollbar]:hidden">
            {featuredScenarios.map((scenario) => (
              <button
                key={scenario.id}
                type="button"
                aria-label={`启动场景：${scenario.title}`}
                onClick={() => launchScenario(scenario)}
                className={cn(
                  "group relative min-h-44 w-[280px] shrink-0 overflow-hidden rounded-2xl border border-border-subtle bg-gradient-to-br p-4 text-left shadow-none transition-[border-color,transform] hover:-translate-y-0.5 hover:border-border-default sm:w-[300px]",
                  scenario.accent,
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h4 className="text-base font-semibold tracking-tight text-foreground">
                      {scenario.title}
                    </h4>
                    <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
                      {scenario.description}
                    </p>
                  </div>
                  <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-background/70 text-muted-foreground transition-transform group-hover:translate-x-0.5">
                    <ArrowRightIcon className="size-3.5" />
                  </span>
                </div>
                <div className="mt-3 space-y-1.5">
                  {scenario.members.slice(0, 3).map((member) => (
                    <div
                      key={member.id}
                      className="flex items-center gap-2 text-xs font-medium text-foreground/90"
                    >
                      <span className="flex size-6 shrink-0 items-center justify-center overflow-hidden rounded-full border border-background/80 bg-background/70 text-[11px]">
                        {member.avatar_url ? (
                          <img
                            src={member.avatar_url}
                            alt=""
                            className="size-full object-cover"
                          />
                        ) : (
                          member.icon || "·"
                        )}
                      </span>
                      <span className="truncate">{member.display_name}</span>
                    </div>
                  ))}
                </div>
                {scenario.members.length > 3 ? (
                  <span className="absolute bottom-4 right-4 text-[11px] text-muted-foreground">
                    +{scenario.members.length - 3} 位成员
                  </span>
                ) : null}
              </button>
            ))}
          </div>
        </section>
      ) : null}

      {!sceneOnly ? (
        <>
          <div className="flex items-center justify-between pt-1">
            <h3 className="text-sm font-semibold text-foreground">全部角色</h3>
            <span className="text-[11px] text-muted-foreground">
              {visibleAgents.length} 位
            </span>
          </div>

          {visibleAgents.length > 0 ? (
            <div
              data-testid="agents-card-grid"
              className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3 min-[1800px]:grid-cols-4"
            >
              {visibleAgents.map((agent) =>
                agent.is_installed ? (
                  <AgentCard
                    key={agent.id}
                    agent={worldAgentToAgent(agent)}
                    isDefault={
                      agent.is_official || LOCAL_AGENT_IDS.has(agent.id)
                    }
                    isPrimaryIdentity={isPrimaryPersonaAgentId(agent.id)}
                    onSelect={() => onSelectAgent(agent)}
                  />
                ) : (
                  <AgentWorldCard
                    key={agent.id}
                    agent={agent}
                    onSelect={onSelectAgent}
                    onInstallChange={onInstallChange}
                  />
                ),
              )}
            </div>
          ) : (
            <div
              data-testid="agents-empty-state"
              className="flex flex-col items-center py-16"
              role="status"
            >
              <StoreIcon className="text-muted-foreground/30 mb-3 h-10 w-10" />
              <p className="text-muted-foreground text-sm">
                {t.agentWorld.noAgentsFound}
              </p>
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Plugins Tab (extracted from plugins page, embedded into Hub)
// ---------------------------------------------------------------------------

import {
  listPlugins,
  hubListPlugins,
  hubGetPluginConfig,
  hubUpdatePluginConfig,
} from "@/core/plugins/api";
import type { PluginInfo, HubPluginInfo } from "@/core/plugins/types";
import { getBackendBaseURL } from "@/core/config";
import {
  CheckCircle as CheckCircleIcon,
  XCircle as XCircleIcon,
  Settings2 as Settings2Icon,
} from "lucide-react";
import { useOpenCreatePluginChat } from "@/components/store/store-utils";

type PluginEntry =
  | { plugin: HubPluginInfo; source: "hub" }
  | { plugin: PluginInfo; source: "legacy" };
type PluginStatusFilter = "all" | "enabled" | "disabled";

function pluginImageUrl(plugin: PluginInfo | HubPluginInfo): string | null {
  const p = plugin as PluginInfo;
  const raw = p.logo_url || p.icon_url;
  if (!raw) return null;
  if (raw.startsWith("http://") || raw.startsWith("https://")) return raw;
  return `${getBackendBaseURL()}${raw}`;
}

function HubPluginConfigDialog({
  plugin,
  open,
  onOpenChange,
}: {
  plugin: HubPluginInfo;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useI18n();
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      hubGetPluginConfig(plugin.id)
        .then(setConfig)
        .catch((e) => swallow(e));
    }
  }, [plugin.id, open]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await hubUpdatePluginConfig(plugin.id, config);
      onOpenChange(false);
    } catch (e) {
      swallow(e);
    } finally {
      setSaving(false);
    }
  };

  const schema = plugin.config_schema as
    | {
        properties?: Record<
          string,
          {
            type?: string;
            title?: string;
            description?: string;
            format?: string;
          }
        >;
      }
    | undefined;
  const properties = schema?.properties;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t.plugins.configureTitle(plugin.name)}</DialogTitle>
          <DialogDescription>
            {t.plugins.configureDescription(plugin.name)}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {properties && Object.keys(properties).length > 0 ? (
            Object.entries(properties).map(([key, prop]) => (
              <div key={key} className="space-y-1">
                <Label htmlFor={`cfg-${key}`}>{prop.title || key}</Label>
                {prop.description && (
                  <p className="text-xs text-muted-foreground">
                    {prop.description}
                  </p>
                )}
                <Input
                  id={`cfg-${key}`}
                  type={
                    prop.format === "password"
                      ? "password"
                      : prop.type === "integer"
                        ? "number"
                        : "text"
                  }
                  value={String(config[key] ?? "")}
                  onChange={(e) =>
                    setConfig((prev) => ({
                      ...prev,
                      [key]:
                        prop.type === "integer"
                          ? parseInt(e.target.value) || 0
                          : e.target.value,
                    }))
                  }
                />
              </div>
            ))
          ) : (
            <p className="text-sm text-muted-foreground">
              {t.plugins.configureNoConfig}
            </p>
          )}
        </div>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">{t.plugins.configureCancel}</Button>
          </DialogClose>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? t.plugins.configureSaving : t.plugins.configureSave}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PluginListItem({
  entry,
  onConfigure,
}: {
  entry: PluginEntry;
  onConfigure: (plugin: HubPluginInfo) => void;
}) {
  const { t } = useI18n();
  const { plugin } = entry;
  const hubPlugin = entry.source === "hub" ? entry.plugin : null;
  const imageUrl = pluginImageUrl(plugin);
  const hasConfig = Boolean(
    hubPlugin?.config_schema && Object.keys(hubPlugin.config_schema).length > 0,
  );
  const statusTitle = plugin.error
    ? t.plugins.statusErrorTooltip
    : plugin.enabled
      ? t.plugins.statusEnabledTooltip
      : t.plugins.statusDisabledTooltip;

  return (
    <Card className="group flex flex-col gap-3 border border-border bg-card p-3 shadow-none transition-colors hover:bg-accent/30 sm:flex-row sm:items-center">
      <div
        className={cn(
          "flex size-11 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-border bg-background",
          !plugin.enabled && "bg-muted/40",
        )}
      >
        {imageUrl ? (
          <img
            src={imageUrl}
            alt=""
            className="size-8 object-contain"
            loading="lazy"
          />
        ) : (
          <PuzzleIcon
            className={cn(
              "size-5",
              plugin.enabled ? "text-primary" : "text-muted-foreground",
            )}
          />
        )}
      </div>
      <CardContent className="min-w-0 flex-1 p-0">
        <div className="flex min-w-0 items-center gap-2">
          <h3 className="truncate text-sm font-semibold leading-5">
            {plugin.name}
          </h3>
        </div>
        <p className="mt-0.5 line-clamp-1 text-sm leading-5 text-muted-foreground">
          {plugin.description}
        </p>
      </CardContent>
      <div className="flex shrink-0 items-center gap-1.5">
        {hasConfig && hubPlugin && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={t.plugins.configureTitle(plugin.name)}
            className="size-8"
            onClick={() => onConfigure(hubPlugin)}
          >
            <Settings2Icon className="size-4" />
          </Button>
        )}
        <span
          title={statusTitle}
          className={cn(
            "flex size-8 items-center justify-center rounded-lg transition-colors",
            plugin.error
              ? "bg-destructive/10 text-destructive"
              : plugin.enabled
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-muted",
          )}
        >
          {plugin.error ? (
            <XCircleIcon className="size-5" />
          ) : plugin.enabled ? (
            <CheckCircleIcon className="size-5" />
          ) : (
            <PlusIcon className="size-5" />
          )}
        </span>
      </div>
    </Card>
  );
}

function PluginsTabContent({ searchQuery }: { searchQuery: string }) {
  const { t } = useI18n();
  const openCreatePluginChat = useOpenCreatePluginChat();
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [hubPlugins, setHubPlugins] = useState<HubPluginInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [configTarget, setConfigTarget] = useState<HubPluginInfo | null>(null);
  const [pluginAuthorFilter, setPluginAuthorFilter] = useState("all");
  const [pluginStatusFilter, setPluginStatusFilter] =
    useState<PluginStatusFilter>("all");

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [legacy, hub] = await Promise.all([
        listPlugins().catch(() => [] as PluginInfo[]),
        hubListPlugins().catch(() => [] as HubPluginInfo[]),
      ]);
      setPlugins(legacy);
      setHubPlugins(hub);
    } catch (e) {
      swallow(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const pluginEntries = useMemo<PluginEntry[]>(() => {
    const hubEntries = hubPlugins
      .filter((plugin) => plugin.id !== "openproject-pm")
      .map((plugin) => ({ plugin, source: "hub" as const }));
    const legacyEntries = plugins.map((plugin) => ({
      plugin,
      source: "legacy" as const,
    }));
    return [...hubEntries, ...legacyEntries].sort((a, b) =>
      a.plugin.name.localeCompare(b.plugin.name),
    );
  }, [hubPlugins, plugins]);

  const pluginAuthors = useMemo(() => {
    return Array.from(
      new Set(pluginEntries.map(({ plugin }) => plugin.author).filter(Boolean)),
    ).sort((a, b) => a.localeCompare(b));
  }, [pluginEntries]);

  const filteredPluginEntries = useMemo(() => {
    const needle = searchQuery.trim().toLowerCase();
    return pluginEntries.filter(({ plugin }) => {
      if (
        pluginAuthorFilter !== "all" &&
        plugin.author !== pluginAuthorFilter
      ) {
        return false;
      }
      if (pluginStatusFilter === "enabled" && !plugin.enabled) return false;
      if (pluginStatusFilter === "disabled" && plugin.enabled) return false;
      if (!needle) return true;
      return [
        plugin.name,
        plugin.description,
        plugin.author,
        plugin.version,
        plugin.state,
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [pluginAuthorFilter, pluginEntries, searchQuery, pluginStatusFilter]);

  if (loading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <PuzzleIcon className="size-8 animate-pulse text-primary" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <Tabs defaultValue="local">
        <TabsList variant="line" className="mb-1">
          <TabsTrigger value="local" className="h-8 gap-1.5 px-3 text-xs">
            {t.agentWorldUnified.enabledTab}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="local" className="mt-0 flex flex-col gap-4">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="flex flex-wrap items-center gap-2">
              <Select
                value={pluginAuthorFilter}
                onValueChange={setPluginAuthorFilter}
              >
                <SelectTrigger className="h-9 w-auto gap-2 rounded-lg bg-background shadow-none">
                  <SelectValue>
                    {pluginAuthorFilter === "all"
                      ? t.plugins.filterAllAuthors
                      : t.plugins.filterByAuthor(pluginAuthorFilter)}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">
                    {t.plugins.filterAllAuthors}
                  </SelectItem>
                  {pluginAuthors.map((author) => (
                    <SelectItem key={author} value={author}>
                      {t.plugins.filterByAuthor(author)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select
                value={pluginStatusFilter}
                onValueChange={(value) =>
                  setPluginStatusFilter(value as PluginStatusFilter)
                }
              >
                <SelectTrigger className="h-9 w-auto gap-2 rounded-lg bg-background shadow-none">
                  <SelectValue>
                    {pluginStatusFilter === "all" && t.plugins.statusAll}
                    {pluginStatusFilter === "enabled" &&
                      t.plugins.statusEnabledFilter}
                    {pluginStatusFilter === "disabled" &&
                      t.plugins.statusDisabledFilter}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t.plugins.statusAll}</SelectItem>
                  <SelectItem value="enabled">
                    {t.plugins.statusEnabledFilter}
                  </SelectItem>
                  <SelectItem value="disabled">
                    {t.plugins.statusDisabledFilter}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-9 rounded-lg px-3 text-xs"
              onClick={openCreatePluginChat}
            >
              <PlusIcon className="mr-1.5 size-3.5" />
              {t.common.create}
            </Button>
          </div>

          {filteredPluginEntries.length > 0 ? (
            <div className="grid grid-cols-[repeat(auto-fit,minmax(320px,1fr))] gap-3">
              {filteredPluginEntries.map((entry) => (
                <PluginListItem
                  key={`${entry.source}-${entry.plugin.id}`}
                  entry={entry}
                  onConfigure={setConfigTarget}
                />
              ))}
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-border bg-muted/10 px-6 py-12 text-center">
              <PuzzleIcon className="mx-auto mb-3 size-10 text-muted-foreground/30" />
              <p className="text-sm text-muted-foreground">
                {pluginEntries.length === 0
                  ? t.plugins.emptyTitle
                  : t.plugins.noMatches}
              </p>
              <p className="mt-1 text-xs text-muted-foreground/60">
                {pluginEntries.length === 0
                  ? t.plugins.emptyHint
                  : t.plugins.tryDifferentQuery}
              </p>
            </div>
          )}

          {configTarget && (
            <HubPluginConfigDialog
              plugin={configTarget}
              open={true}
              onOpenChange={(open) => {
                if (!open) setConfigTarget(null);
              }}
            />
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}

// Kept as a compatibility implementation while legacy callers migrate to
// AppMarketplacePanel. It is intentionally not rendered by the HUB surface.
void PluginsTabContent;

// ---------------------------------------------------------------------------
// Main Unified Component
// ---------------------------------------------------------------------------

// Hub shows all available agents (installed + installable).
const LOCAL_LIBRARY_INSTALLED_ONLY = false;
// Only the system-level admin persona is hidden from the hub;
// desktop_operator (Raven) is a first-class user-facing CUA persona
// since #22 (CUA productization).
const HIDDEN_LOCAL_AGENT_IDS = new Set(["admin"]);

export type HubMarketSection = "agents" | "applications" | "skills";
export type HubApplicationView =
  | "featured"
  | "all"
  | "installed"
  | "codex"
  | "skills"
  | "library"
  | "remote";
export type HubTalentView = "roles" | "cloud" | "experts" | "teams" | "remote";

export function resolveHubMarketRoute(search: string): {
  section: HubMarketSection;
  applicationView: HubApplicationView;
} {
  const tab = new URLSearchParams(search).get("tab");
  if (tab === "agents" || tab === "enterprise") {
    return { section: "agents", applicationView: "all" };
  }
  if (tab === "skills" || tab === "skill-packs") {
    return { section: "skills", applicationView: "all" };
  }
  if (tab === "plugins" || tab === "packs") {
    const view = new URLSearchParams(search).get("view");
    return {
      section: "applications",
      applicationView:
        view === "installed" || view === "all" ? view : "featured",
    };
  }
  if (tab === "codex-plugins") {
    return { section: "applications", applicationView: "codex" };
  }
  if (tab === "assets") {
    return { section: "applications", applicationView: "all" };
  }
  return { section: "agents", applicationView: "all" };
}

export function resolveHubTalentView(search: string): HubTalentView {
  const talent = new URLSearchParams(search).get("talent");
  if (
    talent === "cloud" ||
    talent === "experts" ||
    talent === "teams" ||
    talent === "remote"
  ) {
    return talent;
  }
  return "roles";
}

export function AgentWorldUnified() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const activeAgentId = useActiveAgentId() ?? DEFAULT_PRIMARY_AGENT_ID;
  const enabledModuleIds = useEnabledModuleIds(activeAgentId);
  const enabledModuleIdSet = useMemo(
    () => new Set(enabledModuleIds),
    [enabledModuleIds],
  );

  // State
  const [searchQuery, setSearchQuery] = useState("");
  const marketRoute = useMemo(
    () => resolveHubMarketRoute(location.search),
    [location.search],
  );
  const pluginDirectoryView =
    marketRoute.applicationView === "featured" ||
    marketRoute.applicationView === "installed"
      ? marketRoute.applicationView
      : "all";
  const [activeMarket, setActiveMarket] = useState<HubMarketSection>(
    () => marketRoute.section,
  );
  const [selectedAgent, setSelectedAgent] = useState<AgentWorldAgent | null>(
    null,
  );
  const [hubSmartTeamOpen, setHubSmartTeamOpen] = useState(false);
  const hudOnly = new URLSearchParams(location.search).get("hud") === "1";
  const requestedAgentName =
    new URLSearchParams(location.search).get("agent")?.trim() || "";

  // Data
  const [agents, setAgents] = useState<AgentWorldAgent[]>([]);
  const [loading, setLoading] = useState(true);
  const [agentsLoadError, setAgentsLoadError] = useState(false);
  const [installedWorkbenchPackages, setInstalledWorkbenchPackages] = useState<
    Set<string>
  >(new Set());
  const [workbenchPackageLoading, setWorkbenchPackageLoading] = useState(false);
  const [workbenchPackageMutating, setWorkbenchPackageMutating] = useState<
    Set<string>
  >(new Set());
  const [runtimeWorkbenchStatuses, setRuntimeWorkbenchStatuses] = useState<
    Map<string, RuntimePluginStatus>
  >(new Map());
  const [workbenchPackageStatuses, setWorkbenchPackageStatuses] = useState<
    Map<string, RuntimePluginStatus>
  >(new Map());
  const [uninstallWorkbenchApp, setUninstallWorkbenchApp] =
    useState<WorkbenchBuiltinApp | null>(null);
  const [restoreWorkbenchApp, setRestoreWorkbenchApp] =
    useState<WorkbenchBuiltinApp | null>(null);
  const [uninstallDataPolicy, setUninstallDataPolicy] = useState<
    "keep" | "trash"
  >("keep");

  // Fetch agents
  const fetchAgents = useCallback(async () => {
    setLoading(true);
    setAgentsLoadError(false);
    try {
      await waitForBackendAvailability();
      const localAgents = await listLocalAgents();
      setAgents(localAgents.map(localAgentToWorldAgent));
    } catch (e) {
      swallow(e);
      // Preserve the last good scene roster during a later refresh failure.
      // This keeps the HUB usable while the backend reconnects.
      setAgentsLoadError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchAgents();
  }, [fetchAgents]);

  const refreshWorkbenchPackages = useCallback(async () => {
    setWorkbenchPackageLoading(true);
    try {
      const { installed, runtimeStatuses } =
        await loadWorkbenchAvailabilitySnapshot();
      const installedSet = new Set(installed.plugins);
      setInstalledWorkbenchPackages(installedSet);
      setWorkbenchPackageStatuses(
        new Map(Object.entries(installed.plugin_states ?? {})),
      );
      setRuntimeWorkbenchStatuses(new Map(runtimeStatuses));
      await syncWorkbenchAvailability({ installed, runtimeStatuses });
    } catch (error) {
      swallow(error);
    } finally {
      setWorkbenchPackageLoading(false);
    }
  }, []);

  useEffect(() => {
    if (activeMarket === "applications") void refreshWorkbenchPackages();
  }, [activeMarket, refreshWorkbenchPackages]);

  const mutateWorkbenchPackage = useCallback(
    async (
      app: WorkbenchBuiltinApp,
      operation: "install" | "uninstall" | "enable" | "disable" | "rollback",
      options: {
        dataPolicy?: "keep" | "trash";
        restoreData?: boolean;
        recoveryId?: string;
      } = {},
    ) => {
      if (!app.cloudId || !app.packageId) return;
      setWorkbenchPackageMutating((current) =>
        new Set(current).add(app.packageId as string),
      );
      try {
        if (operation === "install") {
          const result = await installCloudPlugin(app.cloudId, {
            restoreData: options.restoreData,
            recoveryId: options.recoveryId,
          });
          setInstalledWorkbenchPackages((current) =>
            new Set(current).add(app.packageId as string),
          );
          if (app.runtimePlugin) {
            const next = await fetchRuntimePluginStatus(app.runtimePlugin);
            setRuntimeWorkbenchStatuses((current) => {
              const updated = new Map(current);
              updated.set(app.runtimePlugin as string, next);
              return updated;
            });
          }
          setModuleAvailable(app.moduleId, true);
          setModuleEnabled(app.moduleId, true, activeAgentId);
          setRestoreWorkbenchApp(null);
          toast.success(
            result.data?.status === "restored"
              ? `${app.name}已安装，作品已恢复`
              : result.operation === "update"
                ? `${app.name}已更新`
                : `${app.name}已安装`,
          );
        } else if (operation === "uninstall") {
          const result = await uninstallCloudPlugin(app.cloudId, {
            dataPolicy: options.dataPolicy,
            confirmDataMove: options.dataPolicy === "trash",
          });
          setInstalledWorkbenchPackages((current) => {
            const next = new Set(current);
            next.delete(app.packageId as string);
            return next;
          });
          setModuleAvailable(app.moduleId, false);
          if (app.runtimePlugin) {
            setRuntimeWorkbenchStatuses((current) => {
              const updated = new Map(current);
              updated.delete(app.runtimePlugin as string);
              return updated;
            });
          }
          setUninstallWorkbenchApp(null);
          toast.success(
            result.data?.status === "trashed"
              ? `${app.name}已卸载，作品已移入可恢复回收站`
              : `${app.name}已卸载，作品已保留`,
          );
        } else if (operation === "rollback") {
          const packageStatus = workbenchPackageStatuses.get(app.packageId);
          const result = await rollbackCloudPlugin(
            app.cloudId,
            packageStatus?.transaction_id ?? undefined,
          );
          if (!result.installed) {
            setInstalledWorkbenchPackages((current) => {
              const next = new Set(current);
              next.delete(app.packageId as string);
              return next;
            });
            setModuleAvailable(app.moduleId, false);
          }
          toast.success(
            result.operation === "restored_previous"
              ? `${app.name}已回退到上一个版本`
              : `${app.name}的最近安装已撤销`,
          );
        } else {
          const enabled = operation === "enable";
          const next = await setCloudPluginEnabled(app.cloudId, enabled);
          setWorkbenchPackageStatuses((current) => {
            const updated = new Map(current);
            updated.set(app.packageId as string, next);
            return updated;
          });
          if (app.runtimePlugin) {
            setRuntimeWorkbenchStatuses((current) => {
              const updated = new Map(current);
              updated.set(app.runtimePlugin as string, next);
              return updated;
            });
          }
          setModuleAvailable(app.moduleId, enabled);
          if (enabled) setModuleEnabled(app.moduleId, true, activeAgentId);
          toast.success(`${app.name}已${enabled ? "启用" : "停用"}`);
        }
        await refreshWorkbenchPackages();
      } catch (error) {
        toast.error(error instanceof Error ? error.message : String(error));
      } finally {
        setWorkbenchPackageMutating((current) => {
          const next = new Set(current);
          next.delete(app.packageId as string);
          return next;
        });
      }
    },
    [activeAgentId, refreshWorkbenchPackages, workbenchPackageStatuses],
  );

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const nextRoute = resolveHubMarketRoute(location.search);
    setActiveMarket(nextRoute.section);
    if (params.get("connect") === "opencode") {
      setActiveMarket("applications");
      setSearchQuery("OpenCode Zen");
    }
  }, [location.search]);

  // Filter agents
  const allDedupedAgents = useMemo(() => {
    // Hub shows Echo's own user-facing roles only.
    const visibleAgents = agents.filter(
      (agent) =>
        !HIDDEN_LOCAL_AGENT_IDS.has(agent.id) &&
        !/^(?:local_|registry_local_)/.test(agent.name),
    );
    const deduped = dedupeAgentWorldAgents(visibleAgents);
    return LOCAL_LIBRARY_INSTALLED_ONLY
      ? deduped.filter((a) => a.is_installed)
      : deduped;
  }, [agents]);

  // Only fixed primary personas own a HUD and a standalone conversation.
  // Every other role is an on-demand collaborator and belongs in the cloud
  // directory, regardless of whether it has already been downloaded.
  const dedupedAgents = useMemo(
    () =>
      allDedupedAgents.filter((agent) => isPrimaryPersonaAgentId(agent.name)),
    [allDedupedAgents],
  );
  useEffect(() => {
    if (!hudOnly || dedupedAgents.length === 0) return;
    // `?agent=` targets the HUD at one role (the per-row HUD buttons in the
    // bottom-left switcher). It wins over the stored active agent, and it
    // re-selects on change so clicking another row's HUD button switches the
    // panel instead of sticking on the first selection.
    if (requestedAgentName) {
      const requested = resolveHudAgent(
        agents,
        dedupedAgents,
        requestedAgentName,
      );
      if (requested) {
        setSelectedAgent((prev) =>
          prev?.name === requested.name ? prev : requested,
        );
      }
      // An unresolvable target (e.g. a CLI partner, which the HUD roster
      // excludes) opens nothing rather than an unrelated role.
      return;
    }
    if (selectedAgent) return;
    let activeName = "";
    try {
      activeName = window.localStorage.getItem(ACTIVE_AGENT_KEY) ?? "";
    } catch (e) {
      swallow(e, "storage");
    }
    const nextAgent =
      dedupedAgents.find((agent) => agent.name === activeName) ??
      dedupedAgents[0] ??
      null;
    setSelectedAgent(nextAgent);
  }, [agents, dedupedAgents, hudOnly, requestedAgentName, selectedAgent]);

  const handleSelectAgent = useCallback((agent: AgentWorldAgent) => {
    setSelectedAgent(agent);
  }, []);
  const handleSwitchAgent = useCallback((agent: AgentWorldAgent) => {
    setSelectedAgent(agent);
    if (isPrimaryPersonaAgentId(agent.name)) {
      emitAgentChanged(agent.name);
    }
  }, []);

  const handleInstallChange = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["agents"] });
    void fetchAgents();
  }, [fetchAgents, queryClient]);

  const chatRouteForAgent = useCallback((agent: AgentWorldAgent | null) => {
    return taskWorkspaceRoute({ agentId: agent?.name });
  }, []);

  const navigateToHubSection = useCallback(
    (section: HubMarketSection) => {
      const params = new URLSearchParams(location.search);
      params.delete("tab");
      params.delete("talent");
      params.delete("installed");
      params.delete("view");
      if (section === "agents") params.set("tab", "agents");
      if (section === "applications") {
        params.set("tab", "plugins");
        params.set("view", "featured");
      }
      if (section === "skills") params.set("tab", "skills");
      const query = params.toString();
      navigate(`/workspace/agents${query ? `?${query}` : ""}`, {
        replace: true,
      });
    },
    [location.search, navigate],
  );

  const navigateToApplicationView = useCallback(
    (view: "featured" | "all" | "installed") => {
      const params = new URLSearchParams(location.search);
      params.set("tab", "plugins");
      params.set("view", view);
      params.delete("connect");
      const query = params.toString();
      navigate(`/workspace/agents${query ? `?${query}` : ""}`, {
        replace: true,
      });
    },
    [location.search, navigate],
  );

  const searchPlaceholder = "搜索角色、应用或 Skills…";

  return (
    <div className="relative flex size-full flex-col px-2 pb-2 pt-2 md:px-3">
      {!hudOnly ? (
        <div className="-mx-2 -mt-2 flex h-12 shrink-0 items-center gap-2 border-b border-border-subtle bg-background/95 px-2 md:hidden">
          <SidebarTrigger
            className="size-9 shrink-0"
            aria-label={t.common.openSidebarMenu}
            title={t.common.openSidebarMenu}
          />
          <h1 className="min-w-0 truncate text-sm font-semibold">
            {t.agentWorldUnified.pageTitle}
          </h1>
        </div>
      ) : null}
      {/* Main Content */}
      {!hudOnly && (
        <div className="relative flex-1 overflow-y-auto px-3 py-3 md:px-4 md:py-4">
          <Tabs
            value={activeMarket}
            onValueChange={(value) =>
              navigateToHubSection(value as HubMarketSection)
            }
          >
            <header
              data-testid="hub-market-navigation"
              className="mb-4 flex items-center justify-between gap-3 border-b border-border-subtle"
            >
              <h1 className="sr-only">HUB</h1>
              <div className="relative max-w-full after:pointer-events-none after:absolute after:inset-y-0 after:right-0 after:w-7 after:bg-gradient-to-l after:from-background after:to-transparent md:after:hidden">
                <TabsList
                  variant="line"
                  className="mb-0 w-fit justify-start gap-1 overflow-x-auto pr-6 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden md:pr-0"
                >
                  <TabsTrigger value="agents" className="h-9 px-3 text-xs">
                    角色
                  </TabsTrigger>
                  <TabsTrigger
                    value="applications"
                    className="h-9 px-3 text-xs"
                  >
                    应用
                  </TabsTrigger>
                  <TabsTrigger value="skills" className="h-9 px-3 text-xs">
                    Skills
                  </TabsTrigger>
                </TabsList>
              </div>
              <div className="relative w-full max-w-[320px]">
                <SearchIcon className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                  data-testid="agents-search-input"
                  aria-label={searchPlaceholder}
                  placeholder={searchPlaceholder}
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="h-8 rounded-lg border-border-default bg-background pl-8 text-xs shadow-none"
                />
              </div>
            </header>

            <TabsContent value="agents" className="mt-0">
              <h2 className="sr-only">角色</h2>
              <div className="mb-2 flex justify-end gap-1.5">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-8 rounded-md border-violet-500/25 bg-violet-500/5 px-2.5 text-xs text-violet-700 shadow-none hover:bg-violet-500/10 dark:text-violet-300"
                  onClick={() => setHubSmartTeamOpen(true)}
                >
                  <SparklesIcon className="mr-1.5 size-3.5" />
                  智能组队
                </Button>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      className="h-8 shrink-0 rounded-md px-2.5 text-xs text-muted-foreground shadow-none"
                    >
                      添加
                      <ChevronDownIcon className="ml-1 size-3.5" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-44">
                    <DropdownMenuItem
                      onSelect={() => navigate("/workspace/agents/new")}
                    >
                      <BotIcon className="size-4" />
                      创建 AI 成员
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>

              <AgentsTab
                agents={dedupedAgents}
                filteredAgents={dedupedAgents}
                loading={loading}
                loadError={agentsLoadError}
                activeCategory="all"
                onCategoryChange={() => undefined}
                onSelectAgent={handleSelectAgent}
                onInstallChange={handleInstallChange}
                onRetry={() => void fetchAgents()}
                onCreateAgent={() => navigate("/workspace/agents/new")}
                showManagementActions={false}
                sceneOnly
              />

              <SmartTeamDialog
                open={hubSmartTeamOpen}
                onOpenChange={setHubSmartTeamOpen}
                agents={dedupedAgents}
                onInstallChange={handleInstallChange}
              />

              <section
                aria-labelledby="remote-role-directory-title"
                className="mt-5 border-t border-border-subtle pt-4"
              >
                <div className="mb-3">
                  <h3
                    id="remote-role-directory-title"
                    className="text-sm font-semibold text-foreground"
                  >
                    远端角色
                  </h3>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    角色与角色团统一从云端目录按需添加，不占用主角身份。
                  </p>
                </div>
                <WorkBuddyCloudStorePanel
                  embedded
                  showTypeFilter={false}
                  showTeamFilter
                  searchQuery={searchQuery}
                  onInstalled={() => handleInstallChange()}
                />
              </section>
            </TabsContent>

            <TabsContent value="applications" className="mt-0 space-y-4">
              <section aria-labelledby="application-library-title">
                <h2 id="application-library-title" className="sr-only">
                  应用中心
                </h2>
                <div className="mb-6">
                  <h3 className="mb-2 text-sm font-semibold">应用</h3>
                  <div className="grid gap-x-8 sm:grid-cols-2">
                    {WORKBENCH_BUILTIN_APPS.map((app) => {
                      const Icon = BUILTIN_APP_ICONS[app.icon];
                      const isInSidebar = enabledModuleIdSet.has(app.moduleId);
                      const isCore = app.delivery === "core";
                      const packageStatus = app.packageId
                        ? workbenchPackageStatuses.get(app.packageId)
                        : undefined;
                      const isInstalled =
                        isCore ||
                        (packageStatus
                          ? packageStatus.installed
                          : app.packageId
                            ? installedWorkbenchPackages.has(app.packageId)
                            : false);
                      const isMutating = app.packageId
                        ? workbenchPackageMutating.has(app.packageId)
                        : false;
                      const runtimeStatus = app.runtimePlugin
                        ? runtimeWorkbenchStatuses.get(app.runtimePlugin)
                        : undefined;
                      const lifecycleState =
                        packageStatus?.lifecycle_state ??
                        runtimeStatus?.lifecycle_state;
                      const isBroken =
                        lifecycleState === "broken" ||
                        runtimeStatus?.lifecycle_state === "broken";
                      const isIncompatible =
                        lifecycleState === "incompatible" ||
                        runtimeStatus?.lifecycle_state === "incompatible";
                      const isUpdateAvailable =
                        lifecycleState === "update_available";
                      const isRuntimeEnabled = Boolean(
                        isInstalled &&
                        !isBroken &&
                        !isIncompatible &&
                        (packageStatus?.enabled ?? true) &&
                        (runtimeStatus?.enabled ?? true),
                      );
                      const recoveries = packageStatus?.recoveries ?? [];
                      const requestInstall = () => {
                        if (!isInstalled && recoveries.length > 0) {
                          setRestoreWorkbenchApp(app);
                          return;
                        }
                        void mutateWorkbenchPackage(app, "install");
                      };
                      return (
                        <div
                          key={app.id}
                          className="group relative border-b border-border-subtle transition-colors hover:bg-muted/25"
                        >
                          <button
                            type="button"
                            disabled={isMutating}
                            onClick={() => {
                              if (isInstalled && isRuntimeEnabled) {
                                navigate(app.workspaceRoute);
                              } else if (
                                isInstalled &&
                                !isBroken &&
                                !isIncompatible
                              ) {
                                void mutateWorkbenchPackage(app, "enable");
                              } else {
                                requestInstall();
                              }
                            }}
                            className="flex min-h-16 w-full items-center gap-3 px-2 py-2 pr-20 text-left disabled:cursor-wait"
                            aria-label={`${app.name} · ${app.description}`}
                          >
                            <span
                              className={cn(
                                "grid size-9 shrink-0 place-items-center rounded-xl ring-1 ring-inset transition-transform duration-200 group-hover:scale-[1.04]",
                                BUILTIN_APP_ICON_STYLES[app.icon],
                              )}
                            >
                              <Icon className="size-[18px] stroke-[1.8]" />
                            </span>
                            <span className="min-w-0">
                              <span className="block text-sm font-semibold text-foreground">
                                {app.name}
                              </span>
                              <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                                {app.description}
                              </span>
                              {isBroken || isIncompatible ? (
                                <span className="mt-0.5 block text-micro text-destructive">
                                  {isBroken
                                    ? "安装损坏 · 点击修复"
                                    : "版本不兼容 · 点击更新"}
                                </span>
                              ) : isUpdateAvailable ? (
                                <span className="mt-0.5 block text-micro text-primary">
                                  有可用更新
                                </span>
                              ) : isInstalled && !isRuntimeEnabled ? (
                                <span className="mt-0.5 block text-micro text-amber-600 dark:text-amber-300">
                                  已停用 · 点击重新启用
                                </span>
                              ) : !isInstalled && recoveries.length > 0 ? (
                                <span className="mt-0.5 block text-micro text-emerald-600 dark:text-emerald-300">
                                  有可恢复的作品
                                </span>
                              ) : null}
                            </span>
                          </button>
                          {isCore ? (
                            <span className="absolute right-2 top-1/2 -translate-y-1/2 px-2 text-micro font-medium text-muted-foreground">
                              内置
                            </span>
                          ) : isInstalled ? (
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <button
                                  type="button"
                                  className="absolute right-2 top-1/2 grid size-8 -translate-y-1/2 place-items-center text-muted-foreground opacity-70 transition-colors hover:text-foreground group-hover:opacity-100"
                                  aria-label={
                                    isInSidebar
                                      ? `从侧栏移除${app.name}`
                                      : `将${app.name}添加到侧栏`
                                  }
                                >
                                  <MoreHorizontalIcon className="size-4" />
                                </button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end" className="w-44">
                                <DropdownMenuItem
                                  onSelect={() =>
                                    setModuleEnabled(
                                      app.moduleId,
                                      !isInSidebar,
                                      activeAgentId,
                                    )
                                  }
                                >
                                  <PanelLeftIcon className="size-4" />
                                  {isInSidebar ? "从侧栏移除" : "固定到侧栏"}
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  onSelect={() =>
                                    void mutateWorkbenchPackage(
                                      app,
                                      isRuntimeEnabled ? "disable" : "enable",
                                    )
                                  }
                                >
                                  {isRuntimeEnabled ? (
                                    <CirclePauseIcon className="size-4" />
                                  ) : (
                                    <PowerIcon className="size-4" />
                                  )}
                                  {isRuntimeEnabled ? "停用应用" : "启用应用"}
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  onSelect={() =>
                                    void mutateWorkbenchPackage(app, "install")
                                  }
                                >
                                  <RefreshCwIcon className="size-4" />
                                  {isUpdateAvailable
                                    ? "安装可用更新"
                                    : isBroken || isIncompatible
                                      ? "重新安装修复"
                                      : "重新安装"}
                                </DropdownMenuItem>
                                {packageStatus?.rollback_available ? (
                                  <DropdownMenuItem
                                    onSelect={() =>
                                      void mutateWorkbenchPackage(
                                        app,
                                        "rollback",
                                      )
                                    }
                                  >
                                    <RotateCcwIcon className="size-4" />
                                    {packageStatus.rollback_operation ===
                                    "update"
                                      ? "回退上个版本"
                                      : "撤销最近安装"}
                                  </DropdownMenuItem>
                                ) : null}
                                <DropdownMenuItem
                                  className="text-destructive focus:text-destructive"
                                  onSelect={() => {
                                    setUninstallDataPolicy("keep");
                                    setUninstallWorkbenchApp(app);
                                  }}
                                >
                                  <Trash2Icon className="size-4" /> 卸载
                                </DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          ) : (
                            <button
                              type="button"
                              aria-label={
                                isInSidebar
                                  ? `从侧栏移除${app.name}`
                                  : `将${app.name}添加到侧栏`
                              }
                              disabled={isMutating || workbenchPackageLoading}
                              onClick={() => {
                                if (isInSidebar) {
                                  setModuleEnabled(
                                    app.moduleId,
                                    false,
                                    activeAgentId,
                                  );
                                  return;
                                }
                                requestInstall();
                              }}
                              className="absolute right-2 top-1/2 flex h-7 -translate-y-1/2 items-center gap-1 px-2 text-micro font-medium text-primary transition-colors hover:text-primary/75 disabled:text-muted-foreground"
                            >
                              {isMutating ? (
                                <Loader2Icon className="size-3.5 animate-spin" />
                              ) : (
                                <CloudDownloadIcon className="size-3.5" />
                              )}
                              {isInSidebar
                                ? "移除"
                                : isMutating
                                  ? "安装中"
                                  : recoveries.length > 0
                                    ? "恢复"
                                    : "安装"}
                            </button>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
                <div>
                  <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                    <div>
                      <h3 className="text-sm font-semibold">
                        {pluginDirectoryView === "featured"
                          ? "推荐插件"
                          : "插件"}
                      </h3>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {pluginDirectoryView === "featured"
                          ? "精选内置能力，安装后即可为对话、创作和工程任务补充工具。"
                          : "插件、连接器与 MCP；安装状态直接显示在各项中。"}
                      </p>
                    </div>
                    <div
                      role="tablist"
                      aria-label="插件目录视图"
                      className="flex w-fit items-center rounded-lg bg-muted/60 p-1"
                    >
                      {(
                        [
                          ["featured", "推荐"],
                          ["all", "全部"],
                          ["installed", "已安装"],
                        ] as const
                      ).map(([view, label]) => (
                        <button
                          key={view}
                          type="button"
                          role="tab"
                          aria-selected={pluginDirectoryView === view}
                          onClick={() => navigateToApplicationView(view)}
                          className={cn(
                            "h-7 rounded-md px-3 text-xs transition-colors",
                            pluginDirectoryView === view
                              ? "bg-background font-medium text-foreground shadow-sm"
                              : "text-muted-foreground hover:text-foreground",
                          )}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                  </div>
                  {pluginDirectoryView === "featured" ? (
                    <div className="relative mb-4 overflow-hidden rounded-xl border border-primary/15 bg-gradient-to-br from-primary/[0.09] via-background to-violet-500/[0.08] p-4">
                      <div className="pointer-events-none absolute -right-8 -top-10 size-36 rounded-full bg-primary/10 blur-3xl" />
                      <div className="relative flex items-start gap-3">
                        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                          <SparklesIcon className="size-4" />
                        </div>
                        <div>
                          <h4 className="text-sm font-semibold">
                            从这些能力开始
                          </h4>
                          <p className="mt-1 max-w-2xl text-xs leading-5 text-muted-foreground">
                            模型接入、网页操作、文档、表格、演示和可视化均由内置插件提供；需要账号的插件会在安装后引导连接。
                          </p>
                        </div>
                      </div>
                    </div>
                  ) : null}
                  <CapabilityMarketPanel
                    searchQuery={searchQuery}
                    view={pluginDirectoryView}
                    featuredIds={DEFAULT_FEATURED_APP_IDS}
                    maxItems={
                      pluginDirectoryView === "featured" ? 7 : undefined
                    }
                    showToolbar={false}
                    compact
                  />
                </div>
              </section>
            </TabsContent>

            <TabsContent value="skills" className="mt-0">
              <section aria-labelledby="skills-library-title">
                <h2 id="skills-library-title" className="sr-only">
                  Skills
                </h2>
                <CloudSkillsPanel searchQuery={searchQuery} />
              </section>
            </TabsContent>
          </Tabs>
        </div>
      )}

      {selectedAgent ? (
        <Suspense fallback={null}>
          <AgentRoleProfileDialog
            agent={selectedAgent}
            agents={
              hudOnly
                ? dedupedAgents.filter((candidate) =>
                    isPrimaryPersonaAgentId(candidate.name),
                  )
                : dedupedAgents
            }
            open
            onInstallChange={handleInstallChange}
            onOpenChange={(nextOpen) => {
              if (!nextOpen) {
                const returnRoute = hudOnly
                  ? chatRouteForAgent(selectedAgent)
                  : "";
                setSelectedAgent(null);
                if (hudOnly) navigate(returnRoute);
              }
            }}
            onSelectAgent={handleSwitchAgent}
            onCreateAgent={() => navigate("/workspace/agents/new?return=hud")}
          />
        </Suspense>
      ) : null}

      <Dialog
        open={Boolean(restoreWorkbenchApp)}
        onOpenChange={(open) => {
          if (!open) setRestoreWorkbenchApp(null);
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>恢复{restoreWorkbenchApp?.name}作品</DialogTitle>
            <DialogDescription>
              检测到此前卸载时保存的作品。应用代码会重新下载，作品是否恢复由你决定。
            </DialogDescription>
          </DialogHeader>
          <div className="rounded-xl border border-emerald-500/25 bg-emerald-500/8 p-3 text-sm">
            <span className="font-medium text-foreground">
              可恢复内容已保留
            </span>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              恢复操作不会覆盖现有作品；若目标位置已有新数据，系统会安全中止。
            </p>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                if (!restoreWorkbenchApp) return;
                void mutateWorkbenchPackage(restoreWorkbenchApp, "install");
              }}
            >
              全新安装
            </Button>
            <Button
              onClick={() => {
                if (!restoreWorkbenchApp?.packageId) return;
                const recovery = workbenchPackageStatuses.get(
                  restoreWorkbenchApp.packageId,
                )?.recoveries?.[0];
                void mutateWorkbenchPackage(restoreWorkbenchApp, "install", {
                  restoreData: true,
                  recoveryId: recovery?.recovery_id,
                });
              }}
            >
              安装并恢复作品
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(uninstallWorkbenchApp)}
        onOpenChange={(open) => {
          if (!open) setUninstallWorkbenchApp(null);
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>卸载{uninstallWorkbenchApp?.name}</DialogTitle>
            <DialogDescription>
              应用代码、MCP 与应用 Skills 会立即撤销。请选择作品数据的处理方式。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <button
              type="button"
              onClick={() => setUninstallDataPolicy("keep")}
              className={cn(
                "w-full rounded-xl border p-3 text-left transition",
                uninstallDataPolicy === "keep"
                  ? "border-primary bg-primary/8"
                  : "border-border hover:bg-muted/40",
              )}
            >
              <span className="block text-sm font-semibold">
                保留作品（推荐）
              </span>
              <span className="mt-1 block text-xs text-muted-foreground">
                以后重新安装即可继续使用现有项目。
              </span>
            </button>
            <button
              type="button"
              onClick={() => setUninstallDataPolicy("trash")}
              className={cn(
                "w-full rounded-xl border p-3 text-left transition",
                uninstallDataPolicy === "trash"
                  ? "border-amber-500 bg-amber-500/8"
                  : "border-border hover:bg-muted/40",
              )}
            >
              <span className="block text-sm font-semibold">
                移入可恢复回收站
              </span>
              <span className="mt-1 block text-xs text-muted-foreground">
                不永久删除；重新安装时可以恢复。
              </span>
            </button>
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">取消</Button>
            </DialogClose>
            <Button
              variant="destructive"
              disabled={
                !uninstallWorkbenchApp ||
                Boolean(
                  uninstallWorkbenchApp?.packageId &&
                  workbenchPackageMutating.has(uninstallWorkbenchApp.packageId),
                )
              }
              onClick={() => {
                if (!uninstallWorkbenchApp) return;
                void mutateWorkbenchPackage(
                  uninstallWorkbenchApp,
                  "uninstall",
                  { dataPolicy: uninstallDataPolicy },
                );
              }}
            >
              确认卸载
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
