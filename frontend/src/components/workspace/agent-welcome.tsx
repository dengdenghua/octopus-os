import { BotIcon, PencilIcon } from "lucide-react";
import { useState } from "react";

import { type Agent } from "@/core/agents";
import { withAgentAvatarVersion } from "@/core/agents/avatar";
import {
  getAssistantDisplayName,
  setAssistantDisplayName,
} from "@/core/agents/assistant-naming";
import { getBackendBaseURL } from "@/core/config";
import { cn } from "@/lib/utils";
import { AgentVisualGallery } from "./agent-visual-gallery";

export function AgentWelcome({
  className,
  agent,
  agentName,
}: {
  className?: string;
  agent: Agent | null | undefined;
  agentName: string;
}) {
  const isEcho = agentName === "echo";
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(getAssistantDisplayName);

  const displayName = isEcho
    ? getAssistantDisplayName()
    : (agent?.display_name ??
      agent?.name ??
      (agentName === "general" ? "EchoAI" : agentName));
  const description = agent?.description;
  const typeBadge = isEcho ? "助手" : "Agent";

  const commitRename = () => {
    setAssistantDisplayName(draft);
    setRenaming(false);
  };

  // Check if agent has visual illustrations (for ECHO characters, etc.)
  const hasVisuals =
    agent?.visual_urls && Object.keys(agent.visual_urls).length > 0;

  return (
    <div
      className={cn(
        "mx-auto flex w-full flex-col items-center justify-center gap-4 px-5 py-6 text-center sm:px-8",
        className,
      )}
    >
      <div className="relative">
        <div className="relative flex size-[80px] items-center justify-center overflow-hidden rounded-lg border border-border bg-card">
          {agent?.avatar_url ? (
            <img
              src={`${getBackendBaseURL()}${withAgentAvatarVersion(agent.avatar_url)}`}
              alt={displayName}
              className="h-full w-full object-cover"
            />
          ) : (
            <span className="flex h-full w-full items-center justify-center bg-muted/40">
              <BotIcon className="text-primary h-9 w-9" strokeWidth={1.5} />
            </span>
          )}
        </div>
        <span className="absolute -right-1.5 -bottom-1.5 flex h-5.5 items-center gap-1 rounded-lg border border-border bg-background px-2 text-xs font-semibold tracking-wide text-muted-foreground/90">
          <span className="size-1.5 rounded-full bg-success" />
          {typeBadge}
        </span>
      </div>
      <div className="space-y-2">
        {isEcho && renaming ? (
          <div className="flex items-center justify-center gap-2">
            <input
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") commitRename();
                if (e.key === "Escape") {
                  setDraft(getAssistantDisplayName());
                  setRenaming(false);
                }
              }}
              onBlur={commitRename}
              maxLength={20}
              className="h-8 w-48 rounded-lg border border-border bg-card px-2 text-center text-lg font-bold tracking-tight outline-none focus:border-primary/50"
            />
          </div>
        ) : (
          <h2 className="group inline-flex items-center gap-1.5 text-xl font-bold tracking-tight text-foreground">
            {displayName}
            {isEcho && (
              <button
                type="button"
                aria-label="重命名助手"
                title="重命名助手"
                onClick={() => {
                  setDraft(getAssistantDisplayName());
                  setRenaming(true);
                }}
                className="rounded-md p-1 text-muted-foreground/50 opacity-0 transition-opacity hover:bg-muted/60 hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
              >
                <PencilIcon className="size-3.5" />
              </button>
            )}
          </h2>
        )}
        {isEcho ? (
          <p className="text-muted-foreground/80 max-w-md text-sm leading-relaxed">
            今天帮你做些什么？可以随时 @ 引用文件、/ 调用技能，我随时都在。
          </p>
        ) : description ? (
          <p className="text-muted-foreground/80 max-w-md text-sm leading-relaxed">
            {description}
          </p>
        ) : (
          <p className="text-muted-foreground/70 max-w-md text-sm leading-relaxed">
            Ready for the next turn.
          </p>
        )}
      </div>

      {/* Display character illustrations if available */}
      {hasVisuals && (
        <AgentVisualGallery
          visualUrls={agent.visual_urls}
          agentName={displayName}
          className="mt-6 max-w-sm"
        />
      )}
    </div>
  );
}
