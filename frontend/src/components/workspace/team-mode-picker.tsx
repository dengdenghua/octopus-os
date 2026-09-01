import {
  BoxesIcon,
  ChevronDownIcon,
  FlagIcon,
  GitBranchIcon,
  MessageCircleIcon,
  type LucideIcon,
} from "lucide-react";
import { useMemo } from "react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import type { Translations } from "@/core/i18n/locales/types";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

/**
 * How the team works on a turn:
 * - chat (群聊): @someone routes to that agent; a multi-member room without an
 *   @ mention stays a human discussion instead of waking every assistant.
 * - cluster (集群): the leader decomposes → dispatches → each role works → merges.
 * - swarm (蜂群): agents react to a shared blackboard, parallel & leaderless.
 * - project (项目): milestone-driven — handed to the Project OS to break into
 *   a task DAG, execute, and gate on acceptance.
 * The backend still auto-picks cluster vs swarm by graph shape when no explicit
 * pick is sent; choosing 集群/蜂群 forces that engine (serve_mesh "0"/"1").
 */
export type TeamMode = "chat" | "cluster" | "swarm" | "project";
export type TeamResponseMode = Exclude<TeamMode, "project">;

export type LegacyTeamMode =
  | "cowork"
  | "group_chat"
  | "free"
  | "free_chat"
  | "debate"
  | "pipeline";

export function normalizeTeamMode(
  value: TeamMode | LegacyTeamMode | string | null | undefined,
): TeamMode {
  if (
    value === "cluster" ||
    value === "swarm" ||
    value === "chat" ||
    value === "project"
  ) {
    return value;
  }
  // Legacy: the old single "cowork/group" auto-picked the engine — map it to
  // 集群 (the orchestrated default). Free/group_chat collapse to 单聊.
  if (value === "cowork" || value === "debate" || value === "pipeline") {
    return "cluster";
  }
  return "chat";
}

/** Project is durable context, not a per-turn response strategy. Legacy
 * project-mode groups migrate to normal discussion: explicit @ mentions or
 * message actions decide when AI / Project OS should act. */
export function normalizeTeamResponseMode(
  value: TeamMode | LegacyTeamMode | string | null | undefined,
): TeamResponseMode {
  const normalized = normalizeTeamMode(value);
  return normalized === "project" ? "chat" : normalized;
}

const TEAM_MODE_ICONS: Record<TeamMode, LucideIcon> = {
  chat: MessageCircleIcon,
  cluster: GitBranchIcon,
  swarm: BoxesIcon,
  project: FlagIcon,
};

export const TEAM_MODES: TeamMode[] = ["chat", "cluster", "swarm", "project"];
export const TEAM_RESPONSE_MODES: TeamResponseMode[] = [
  "chat",
  "cluster",
  "swarm",
];

/** Per-turn engine force the backend reads (集群→sequential, 蜂群→mesh). */
export function serveMeshForMode(mode: TeamMode): "0" | "1" | undefined {
  if (mode === "cluster") return "0";
  if (mode === "swarm") return "1";
  return undefined;
}

export type TeamModeMeta = Record<
  TeamMode,
  { label: string; description: string; icon: LucideIcon }
>;

export function getTeamModeMeta(t: Translations): TeamModeMeta {
  const meta = {} as TeamModeMeta;
  for (const mode of TEAM_MODES) {
    const translated = t.collab.teamModes.find((m) => m.id === mode);
    meta[mode] = {
      label: translated?.label ?? mode,
      description: translated?.description ?? "",
      icon: TEAM_MODE_ICONS[mode],
    };
  }
  return meta;
}

export function useTeamModeMeta(): TeamModeMeta {
  const { t } = useI18n();
  return useMemo(() => getTeamModeMeta(t), [t]);
}

export function TeamModePicker({
  value,
  onChange,
  className,
  ariaLabel = "Response mode",
  compact = false,
  disabled = false,
  disabledModes = [],
  disabledReason,
}: {
  value: TeamMode;
  onChange: (mode: TeamResponseMode) => void;
  className?: string;
  ariaLabel?: string;
  compact?: boolean;
  disabled?: boolean;
  disabledModes?: TeamResponseMode[];
  disabledReason?: string;
}) {
  const teamModeMeta = useTeamModeMeta();
  const normalizedValue = normalizeTeamResponseMode(value);
  const disabledSet = useMemo(() => new Set(disabledModes), [disabledModes]);
  const activeMeta = teamModeMeta[normalizedValue];
  const ActiveIcon = activeMeta.icon;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          aria-label={`${ariaLabel}: ${activeMeta.label}`}
          title={`${ariaLabel}: ${activeMeta.label}`}
          data-testid="team-mode-picker"
          className={cn(
            "inline-flex shrink-0 items-center justify-center gap-1 rounded-md border border-transparent bg-transparent px-1.5 text-xs font-medium text-muted-foreground shadow-none transition-all duration-base hover:bg-muted/55 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/35 disabled:cursor-not-allowed disabled:opacity-50",
            compact ? "h-7" : "h-8",
            className,
          )}
        >
          <ActiveIcon className="size-3.5 shrink-0" />
          <span>{activeMeta.label}</span>
          <ChevronDownIcon className="size-3.5 shrink-0 opacity-70" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="center"
        side="top"
        sideOffset={8}
        data-testid="team-mode-menu"
        className="w-[min(19rem,calc(100vw-1rem))] rounded-lg border-border-default p-1.5 shadow-[var(--shadow-xs)]"
      >
        <DropdownMenuLabel className="px-2 py-1 text-xs font-medium text-muted-foreground">
          {ariaLabel}
        </DropdownMenuLabel>
        <DropdownMenuRadioGroup
          value={normalizedValue}
          onValueChange={(nextValue) => onChange(nextValue as TeamResponseMode)}
        >
          {TEAM_RESPONSE_MODES.map((mode) => {
            const meta = teamModeMeta[mode];
            const Icon = meta.icon;
            const modeDisabled = disabledSet.has(mode);
            return (
              <DropdownMenuRadioItem
                key={mode}
                value={mode}
                disabled={modeDisabled}
                data-testid={`team-mode-${mode}`}
                className="items-start gap-2.5 rounded-lg py-2 pr-2 pl-8"
              >
                <Icon className="mt-0.5 size-4 shrink-0" />
                <span className="min-w-0 flex-1">
                  <span className="block text-xs font-medium text-foreground">
                    {meta.label}
                  </span>
                  <span className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">
                    {modeDisabled && disabledReason
                      ? disabledReason
                      : meta.description}
                  </span>
                </span>
              </DropdownMenuRadioItem>
            );
          })}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function TeamModeDescription({
  mode,
  className,
}: {
  mode: TeamMode;
  className?: string;
}) {
  const teamModeMeta = useTeamModeMeta();
  return (
    <p className={cn("text-muted-foreground text-xs", className)}>
      {teamModeMeta[mode].description}
    </p>
  );
}
