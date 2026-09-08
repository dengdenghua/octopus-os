import {
  AlertCircleIcon,
  CheckIcon,
  CoinsIcon,
  LoaderCircleIcon,
  LogOutIcon,
  RefreshCwIcon,
  SettingsIcon,
  UsersRoundIcon,
  UserCircleIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  ROUTE_LOCKS,
  setActiveAgentId,
  useActiveAgentId,
} from "@/core/agents/active";
import { emitAgentChanged, emitOpenSettings } from "@/core/events";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAgents, dedupeAgentsByName } from "@/core/agents";
import type { Agent } from "@/core/agents";
import { isPrimaryPersonaAgentId } from "@/core/agents/persona-policy";
import { withAgentAvatarVersion } from "@/core/agents/avatar";
import { LOCAL_AGENT_RANK } from "@/components/workspace/agents/agent-world-data";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { taskWorkspaceRoute } from "@/core/router/task-workspace-route";
import { preserveWorkbenchPresentation } from "@/core/router/desktop-workspace-route";
import { agentHudHref } from "@/core/workspace/sidebar-routing";
import { useOctLink } from "@/core/oct/hooks";
import { useAuth } from "@/providers/AuthProvider";
import { canAccessOperatorControlPlane } from "@/core/auth/control-plane-access";
import { CreditsCenterDialog } from "@/components/workspace/credits-center";
import { cn } from "@/lib/utils";
import { useEvolutionOverview } from "@/core/evolution/hooks";
import {
  calculateLevel,
  calculateStars,
} from "@/components/workspace/evolution-dashboard/game-data-transformer";

// ─── Helpers ─────────────────────────────────────────────────────

function isPlaceholderUsername(username?: string | null): boolean {
  const value = username?.trim().toLowerCase();
  return !value || value === "anonymous" || value === "__anonymous__";
}

function getAccountDisplayName(user: {
  mobile?: string;
  email?: string;
  username?: string;
  actor_id?: string;
}): string {
  return (
    user.mobile ||
    user.email ||
    (!isPlaceholderUsername(user.username) ? user.username : "") ||
    user.actor_id ||
    ""
  );
}

function sortHubDefaultAgents(left: Agent, right: Agent): number {
  return (
    (LOCAL_AGENT_RANK.get(left.name) ?? Number.MAX_SAFE_INTEGER) -
    (LOCAL_AGENT_RANK.get(right.name) ?? Number.MAX_SAFE_INTEGER)
  );
}

/** Resolve ``Agent.avatar_url`` to an absolute URL the browser can load. */
function resolveAvatarUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  if (
    url.startsWith("http://") ||
    url.startsWith("https://") ||
    url.startsWith("data:") ||
    url.startsWith("blob:")
  ) {
    return withAgentAvatarVersion(url);
  }
  // API avatars belong to the Python gateway. Imported Vite assets must stay
  // on the frontend origin (and may be relative in the packaged Electron app).
  if (url.startsWith("/api/") || url.startsWith("api/")) {
    const path = url.startsWith("/") ? url : `/${url}`;
    return withAgentAvatarVersion(`${getBackendBaseURL()}${path}`);
  }
  return withAgentAvatarVersion(url);
}

// ─── Avatar components ───────────────────────────────────────────

export function AgentAvatar({
  agent,
  className,
}: {
  agent: Agent | undefined;
  className?: string;
}) {
  const avatar = resolveAvatarUrl(agent?.avatar_url);
  const [failedAvatar, setFailedAvatar] = useState<string | null>(null);
  const showAvatar = Boolean(avatar && failedAvatar !== avatar);
  const emoji = agent?.icon?.trim() || "";
  const initial = (agent?.display_name || agent?.name || "?")
    .trim()
    .charAt(0)
    .toUpperCase();
  return (
    <span
      aria-hidden="true"
      className={cn(
        "flex size-6 shrink-0 items-center justify-center overflow-hidden rounded-md border border-border-default bg-muted text-sm leading-none",
        !emoji && !avatar && "font-semibold text-muted-foreground text-xs",
        className,
      )}
    >
      {showAvatar ? (
        <img
          src={avatar ?? undefined}
          alt=""
          className="size-full object-cover"
          loading="lazy"
          onError={() => setFailedAvatar(avatar)}
        />
      ) : emoji ? (
        emoji
      ) : (
        initial
      )}
    </span>
  );
}

// ─── AgentFooter ─────────────────────────────────────────────────

export function AgentFooter() {
  const {
    agents,
    isLoading: agentsLoading,
    isFetching: agentsFetching,
    error: agentsError,
    refetch: refetchAgents,
  } = useAgents();
  const { user, logout, authStatus } = useAuth();
  const _navigate = useNavigate();
  const { pathname, search } = useLocation();
  const octLink = useOctLink();
  const { t } = useI18n();
  const credits = octLink.data?.credits?.surplusCredits;
  const [creditsOpen, setCreditsOpen] = useState(false);
  const activeName = useActiveAgentId();

  // Fetch evolution data for the active agent (no agent filter, gets current user's data)
  const { data: evolutionData } = useEvolutionOverview({
    enabled: canAccessOperatorControlPlane(authStatus, user),
  });
  const lock = ROUTE_LOCKS.find((r) => pathname.startsWith(r.prefix));
  const surfaceParam = new URLSearchParams(search).get("surface");
  const agentLibrarySurface = surfaceParam === "company" ? "company" : "chat";
  const agentLibraryHref = (tab?: string, agentName?: string) =>
    preserveWorkbenchPresentation(
      agentHudHref({ surface: agentLibrarySurface, tab, agentName }),
      search,
    );
  const personaAgents = useMemo(() => {
    // Only the fixed White Ghost squad owns personal conversation identities.
    // Installed experts and digital twins are selected in
    // the task's member control and join that task on demand.
    return dedupeAgentsByName(
      agents
        .filter((agent) => isPrimaryPersonaAgentId(agent.name))
        .sort(sortHubDefaultAgents),
    );
  }, [agents]);
  // The assistant (echo) is a global fixed persona, not a switchable role.
  // It coexists with every other agent but must never surface in the bottom-left
  // persona trigger — even when the current thread belongs to the assistant.
  const switcherAgents = useMemo(() => personaAgents, [personaAgents]);

  const active: Agent | undefined = switcherAgents.find(
    (agent) => agent.name === activeName,
  );

  const lockedAgent: Agent | undefined =
    lock && !agents.find((a) => a.name === lock.agent)
      ? {
          name: lock.agent,
          display_name:
            lock.agent === "admin" ? t.sidebar.adminAgentName : lock.agent,
          description: "",
          icon: lock.agent === "admin" ? "🛡️" : undefined,
          avatar_url: `/api/agents/${lock.agent}/avatar`,
          model: null,
          tool_groups: [],
        }
      : undefined;

  const selectAgent = (name: string) => {
    setActiveAgentId(name);
    emitAgentChanged(name);
    _navigate(
      preserveWorkbenchPresentation(
        taskWorkspaceRoute({ agentId: name }),
        search,
      ),
    );
  };

  // Per-row HUD shortcut. Rendered inside a DropdownMenuItem, so it has to stop
  // both the pointer event and Radix's own `select` from bubbling — otherwise
  // clicking it would also fire the row's `onSelect` and switch agents.
  const renderHudButton = (agentName: string) => (
    <button
      type="button"
      title={t.sidebar.openAgentHud}
      aria-label={t.sidebar.openAgentHudFor(agentName)}
      onPointerDown={(event) => event.stopPropagation()}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        _navigate(agentLibraryHref(undefined, agentName));
      }}
      className={cn(
        "flex size-6 shrink-0 items-center justify-center rounded-md",
        "text-muted-foreground/50 transition-colors",
        "hover:bg-muted hover:text-foreground",
      )}
    >
      <UsersRoundIcon className="size-3.5" />
    </button>
  );

  const renderAgentItem = (a: Agent) => {
    const isActive = a.name === active?.name;
    return (
      <DropdownMenuItem
        key={a.name}
        onSelect={() => selectAgent(a.name)}
        className={cn(
          "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-xs",
          "opacity-85 transition-colors focus:bg-muted/60 focus:text-foreground focus:opacity-100",
          isActive && "bg-muted/35 opacity-100",
        )}
      >
        <AgentAvatar agent={a} className="size-8 rounded-lg text-xs" />
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="truncate font-medium leading-none">
            {a.display_name || a.name}
          </span>
          <span className="truncate text-xs font-normal leading-tight text-muted-foreground">
            {isActive
              ? t.sidebar.currentAgent
              : a.description || t.sidebar.soloChat}
          </span>
        </span>
        {renderHudButton(a.name)}
        {isActive && (
          <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
            <CheckIcon className="size-3" />
          </span>
        )}
      </DropdownMenuItem>
    );
  };

  const displayAgent = lockedAgent ?? active;
  const hasPersonaAgents = personaAgents.length > 0;
  const showAgentLoading =
    !hasPersonaAgents && (agentsLoading || agentsFetching);
  const showAgentError =
    !hasPersonaAgents && !showAgentLoading && Boolean(agentsError);
  const agentTriggerLabel =
    displayAgent?.display_name ||
    displayAgent?.name ||
    (showAgentLoading
      ? t.sidebar.loadingAgents
      : showAgentError
        ? t.sidebar.agentsLoadFailed
        : t.sidebar.noAgents);
  const accountName = user ? getAccountDisplayName(user) : "";

  // Calculate evolution level and stars
  const level = evolutionData
    ? calculateLevel(evolutionData.learning_events)
    : null;
  const stars = level !== null ? calculateStars(level) : null;

  return (
    <div className="flex items-center gap-1">
      <DropdownMenu>
        <DropdownMenuTrigger asChild disabled={Boolean(lock)}>
          <button
            type="button"
            disabled={Boolean(lock)}
            title={
              lock
                ? t.sidebar.lockedAgentTooltip(
                    displayAgent?.display_name || displayAgent?.name || "",
                  )
                : displayAgent?.description || agentTriggerLabel
            }
            aria-label={agentTriggerLabel}
            className={cn(
              "group/agent flex min-w-0 flex-1 items-center gap-2 rounded-md px-1.5 py-1 text-left",
              "opacity-85 transition-[opacity,background-color] duration-fast",
              "hover:opacity-100 hover:bg-muted/50 outline-none",
              "group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0",
            )}
          >
            {displayAgent ? (
              <AgentAvatar agent={displayAgent} />
            ) : (
              <span
                aria-hidden="true"
                className={cn(
                  "flex size-6 shrink-0 items-center justify-center rounded-md border border-border-default bg-muted text-muted-foreground",
                  showAgentError && "text-destructive",
                )}
              >
                {showAgentLoading ? (
                  <LoaderCircleIcon className="size-3.5 animate-spin" />
                ) : showAgentError ? (
                  <AlertCircleIcon className="size-3.5" />
                ) : (
                  <UserCircleIcon className="size-3.5" />
                )}
              </span>
            )}
            <span className="min-w-0 flex-1 truncate text-xs font-medium leading-tight group-data-[collapsible=icon]:hidden">
              {agentTriggerLabel}
              {level !== null && (
                <span className="ml-1.5 text-2xs font-normal text-muted-foreground/80">
                  Lv.{level}
                  {stars !== null && stars > 0 && (
                    <span className="ml-0.5">
                      {"⭐".repeat(Math.min(stars, 5))}
                    </span>
                  )}
                </span>
              )}
            </span>
            {lock ? (
              <span
                className="shrink-0 text-2xs uppercase tracking-wider text-muted-foreground/60 group-data-[collapsible=icon]:hidden"
                aria-hidden
              >
                🔒
              </span>
            ) : (
              <span className="shrink-0 text-muted-foreground/60 group-hover/agent:text-muted-foreground group-data-[collapsible=icon]:hidden">
                <svg
                  viewBox="0 0 24 24"
                  className="size-3"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden
                >
                  <polyline points="6 15 12 9 18 15" />
                </svg>
              </span>
            )}
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          side="top"
          align="start"
          sideOffset={6}
          className="max-h-[calc(100vh-1rem)] w-72 overflow-y-auto overscroll-contain rounded-lg border-border-default p-1.5 shadow-xl shadow-black/10"
        >
          <DropdownMenuLabel className="px-2.5 py-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground/70">
            {t.sidebar.switchAgentMenuTitle}
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          {hasPersonaAgents ? (
            personaAgents.map(renderAgentItem)
          ) : showAgentLoading ? (
            <div className="flex items-center gap-2 px-2.5 py-2 text-xs text-muted-foreground">
              <LoaderCircleIcon className="size-3.5 animate-spin" />
              <span>{t.sidebar.loadingAgents}</span>
            </div>
          ) : showAgentError ? (
            <>
              <div className="flex items-center gap-2 px-2.5 py-2 text-xs text-destructive">
                <AlertCircleIcon className="size-3.5 shrink-0" />
                <span>{t.sidebar.agentsLoadFailed}</span>
              </div>
              <DropdownMenuItem
                onSelect={() => void refetchAgents()}
                className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-xs focus:bg-muted/60 focus:text-foreground"
              >
                <RefreshCwIcon className="size-3.5 shrink-0" />
                <span>{t.sidebar.retryAgents}</span>
              </DropdownMenuItem>
            </>
          ) : (
            <div className="px-2 py-2 text-xs text-muted-foreground">
              {t.sidebar.noAgents}
            </div>
          )}
          <DropdownMenuSeparator />
          {displayAgent ? (
            <>
              <DropdownMenuItem
                onSelect={() => _navigate(agentLibraryHref())}
                className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-xs text-muted-foreground focus:bg-muted/60 focus:text-foreground"
              >
                <UsersRoundIcon className="size-4 shrink-0" />
                <span>{t.sidebar.openAgentHud}</span>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
            </>
          ) : null}
          <DropdownMenuItem
            onSelect={() => setCreditsOpen(true)}
            className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs focus:bg-muted/60"
          >
            <UserCircleIcon className="size-4 shrink-0 opacity-70" />
            <span className="min-w-0 flex-1 truncate text-muted-foreground">
              {accountName}
            </span>
            <CoinsIcon className="size-3.5 shrink-0 opacity-70" />
            <span className="shrink-0 text-xs font-mono text-foreground/80">
              {typeof credits === "number" ? credits.toLocaleString() : "—"}
            </span>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onSelect={() => void logout()}
            className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs focus:bg-muted/60"
          >
            <LogOutIcon className="size-4 shrink-0 opacity-70" />
            <span>{t.sidebar.logout}</span>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <button
        type="button"
        title={t.sidebar.settingsTooltip}
        aria-label={t.sidebar.settingsTooltip}
        onClick={() => emitOpenSettings()}
        className={cn(
          "flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground",
          "opacity-70 transition-[opacity,background-color,color] duration-fast",
          "hover:bg-muted hover:text-foreground hover:opacity-100",
          "group-data-[collapsible=icon]:hidden",
        )}
      >
        <SettingsIcon className="size-4" />
      </button>
      <CreditsCenterDialog open={creditsOpen} onOpenChange={setCreditsOpen} />
    </div>
  );
}
