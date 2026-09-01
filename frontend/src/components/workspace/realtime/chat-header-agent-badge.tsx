/**
 * ChatHeaderAgentBadge — extracted from `workspace/realtime/[thread_id]/page.tsx`
 * (P3 decomposition). Behavior-preserving move.
 */
import type { useAgent } from "@/core/agents";
import { getAssistantDisplayName } from "@/core/agents/assistant-naming";
import { withAgentAvatarVersion } from "@/core/agents/avatar";
import { getBackendBaseURL } from "@/core/config";
import { cn } from "@/lib/utils";

import type { ChatCollaborationRosterEntry } from "./task-collaborator-control";

export function ChatHeaderAgentBadge({
  agent,
  agentId,
  collaborators,
}: {
  agent: ReturnType<typeof useAgent>["agent"];
  agentId: string;
  collaborators?: ChatCollaborationRosterEntry[];
}) {
  const label =
    agentId === "echo"
      ? getAssistantDisplayName()
      : agent?.display_name?.trim() || agent?.name?.trim() || agentId;
  const icon = agent?.icon?.trim() || "";
  const initial = label.trim().charAt(0).toUpperCase() || "A";
  const avatarUrl = agent?.avatar_url
    ? withAgentAvatarVersion(
        agent.avatar_url.startsWith("http://") ||
          agent.avatar_url.startsWith("https://")
          ? agent.avatar_url
          : `${getBackendBaseURL()}${agent.avatar_url}`,
      )
    : "";

  const resolveAvatarUrl = (url?: string | null): string => {
    if (!url) return "";
    return withAgentAvatarVersion(
      url.startsWith("http://") || url.startsWith("https://")
        ? url
        : `${getBackendBaseURL()}${url}`,
    );
  };

  // Multi-agent mode: show avatars side by side
  if (collaborators && collaborators.length > 1) {
    const displayAgents = collaborators.slice(0, 4);
    const extraCount = collaborators.length - displayAgents.length;
    const displayLabel =
      collaborators.length === 2
        ? collaborators.map((a) => a.display_name).join("、")
        : `${collaborators[0]?.display_name || label} 等${collaborators.length}人`;
    return (
      <div
        className="inline-flex h-8 max-w-[220px] shrink-0 items-center gap-1.5 px-1.5 text-xs text-foreground/88"
        title={collaborators.map((a) => a.display_name).join("、")}
      >
        <span className="flex items-center -space-x-1.5">
          {displayAgents.map((collab, index) => {
            const collabAvatar = resolveAvatarUrl(collab.avatar_url);
            const collabInitial = (collab.display_name || collab.name)
              .charAt(0)
              .toUpperCase();
            return (
              <span
                key={collab.agent_id}
                className={cn(
                  "flex size-5 shrink-0 items-center justify-center overflow-hidden rounded-full border-2 border-background bg-muted text-[10px] font-semibold text-muted-foreground",
                  index === 0 && "z-30",
                  index === 1 && "z-20",
                  index === 2 && "z-10",
                )}
              >
                {collabAvatar ? (
                  <img
                    src={collabAvatar}
                    alt={collab.display_name}
                    className="size-full object-cover"
                  />
                ) : collab.icon?.trim() ? (
                  <span className="text-[9px] leading-none">
                    {collab.icon.trim()}
                  </span>
                ) : (
                  collabInitial
                )}
              </span>
            );
          })}
          {extraCount > 0 && (
            <span className="flex size-5 shrink-0 items-center justify-center rounded-full border-2 border-background bg-muted text-[9px] font-semibold text-muted-foreground z-0">
              +{extraCount}
            </span>
          )}
        </span>
        <span className="truncate">{displayLabel}</span>
      </div>
    );
  }

  if (!label || label === "general") return null;
  return (
    <div
      className="inline-flex h-8 max-w-[180px] shrink-0 items-center gap-1.5 px-1.5 text-xs text-foreground/88"
      title={label}
    >
      <span className="flex size-5 shrink-0 items-center justify-center overflow-hidden rounded-full bg-muted text-xs font-semibold text-muted-foreground">
        {avatarUrl ? (
          <img src={avatarUrl} alt={label} className="size-full object-cover" />
        ) : icon ? (
          icon
        ) : (
          initial
        )}
      </span>
      <span className="truncate">{label}</span>
    </div>
  );
}
