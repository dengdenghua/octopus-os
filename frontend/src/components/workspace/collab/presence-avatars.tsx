import { BotIcon, UsersIcon } from "lucide-react";
import type { CSSProperties } from "react";

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useCollab } from "./collab-provider";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

/** A team AI-roster member shown as "in attendance" beside live humans. */
export interface PresenceAgent {
  name: string;
  display_name?: string | null;
  icon?: string | null;
  avatar_url?: string | null;
}

interface PresenceAvatarsProps {
  className?: string;
  /** The team's agent members — always present (no WS session of their own). */
  agents?: PresenceAgent[];
}

const MAX_HUMANS = 4;
const MAX_AGENTS = 5;

export function PresenceAvatars({
  className,
  agents = [],
}: PresenceAvatarsProps) {
  const { t } = useI18n();
  const { users, currentUser } = useCollab();

  const displayUsers = users.slice(0, MAX_HUMANS);
  const remainingUsers = Math.max(0, users.length - MAX_HUMANS);
  const displayAgents = agents.slice(0, MAX_AGENTS);
  const remainingAgents = Math.max(0, agents.length - MAX_AGENTS);

  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className="flex items-center gap-1">
        {/* Live human collaborators (real WS sessions) */}
        {displayUsers.map((user) => {
          const avatarStyle = {
            "--presence-color": user.color,
          } as CSSProperties;

          return (
            <Tooltip key={user.id}>
              <TooltipTrigger asChild>
                <div
                  className="relative flex h-8 shrink-0 items-center"
                  style={avatarStyle}
                >
                  {user.avatar ? (
                    <img
                      src={user.avatar}
                      alt={user.name}
                      className="size-5 rounded object-cover"
                    />
                  ) : (
                    <span className="px-0.5 text-xs font-semibold text-[color:color-mix(in_oklch,var(--presence-color)_72%,var(--foreground))]">
                      {user.name.charAt(0).toUpperCase()}
                    </span>
                  )}
                  {user.id === currentUser?.id && (
                    <span className="absolute -bottom-0.5 left-1/2 size-1.5 -translate-x-1/2 rounded-full bg-success" />
                  )}
                </div>
              </TooltipTrigger>
              <TooltipContent>{user.name}</TooltipContent>
            </Tooltip>
          );
        })}
        {remainingUsers > 0 && (
          <Tooltip>
            <TooltipTrigger asChild>
              <div className="flex h-8 shrink-0 items-center px-0.5 text-xs font-medium text-muted-foreground">
                +{remainingUsers}
              </div>
            </TooltipTrigger>
            <TooltipContent>
              {t.collab.onlineCount(remainingUsers)}
            </TooltipContent>
          </Tooltip>
        )}

        {/* Divider between humans and AI members */}
        {displayUsers.length > 0 && displayAgents.length > 0 && (
          <span className="mx-0.5 h-4 w-px shrink-0 bg-border/60" />
        )}

        {/* AI roster — always in attendance (no live session of their own) */}
        {displayAgents.map((agent) => {
          const name = agent.display_name?.trim() || agent.name;
          return (
            <Tooltip key={agent.name}>
              <TooltipTrigger asChild>
                <div className="relative flex h-8 shrink-0 items-center">
                  {agent.avatar_url ? (
                    <img
                      src={agent.avatar_url}
                      alt={name}
                      className="size-5 rounded object-cover opacity-90"
                    />
                  ) : agent.icon?.trim() ? (
                    <span className="text-base leading-none opacity-90">
                      {agent.icon}
                    </span>
                  ) : (
                    <span className="flex size-5 items-center justify-center rounded bg-muted text-xs font-semibold text-muted-foreground">
                      {name.charAt(0).toUpperCase()}
                    </span>
                  )}
                  <span className="absolute -bottom-0.5 -right-0.5 flex size-2.5 items-center justify-center rounded-full bg-background">
                    <BotIcon className="size-2 text-muted-foreground" />
                  </span>
                </div>
              </TooltipTrigger>
              <TooltipContent>{name} · 随时待命</TooltipContent>
            </Tooltip>
          );
        })}
        {remainingAgents > 0 && (
          <Tooltip>
            <TooltipTrigger asChild>
              <div className="flex h-8 shrink-0 items-center px-0.5 text-xs font-medium text-muted-foreground">
                +{remainingAgents}
              </div>
            </TooltipTrigger>
            <TooltipContent>{remainingAgents} 个 Agent 在场</TooltipContent>
          </Tooltip>
        )}
      </div>

      {/* Summary: AI members in attendance + live humans online */}
      <div className="flex h-8 items-center gap-2 text-xs text-muted-foreground">
        {agents.length > 0 && (
          <span className="flex items-center gap-1">
            <BotIcon className="size-3.5" />
            {agents.length} 在场
          </span>
        )}
        <span className="flex items-center gap-1">
          <UsersIcon className="size-3.5" />
          {t.collab.onlineCount(users.length)}
        </span>
      </div>
    </div>
  );
}
