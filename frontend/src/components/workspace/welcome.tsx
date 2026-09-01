import { useSearchParams } from "react-router-dom";
import { useMemo } from "react";

import { type Agent, useAgents } from "@/core/agents";
import { useActiveAgentId } from "@/core/agents/active";
import { getAssistantDisplayName } from "@/core/agents/assistant-naming";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

/** Pseudo agent IDs used in URLs that are not real agent names. */
const PSEUDO_AGENT_IDS = new Set(["", "new", "general", "echo-assistant"]);

function agentDisplayName(a: Agent | null | undefined): string | null {
  if (!a) return null;
  const d = a.display_name?.trim();
  if (d) return d;
  const n = a.name?.trim();
  if (n && !PSEUDO_AGENT_IDS.has(n)) return n;
  return null;
}

function pickGreetingName(
  agentProp: Agent | null | undefined,
  agentNameProp: string | null | undefined,
  allAgents: Agent[],
  footerAgentId: string | null,
): string {
  if (agentNameProp === "echo") return getAssistantDisplayName();

  const propDisplay = agentDisplayName(agentProp);
  if (propDisplay) return propDisplay;

  const nameFromProp = agentNameProp?.trim() ?? "";
  if (nameFromProp && !PSEUDO_AGENT_IDS.has(nameFromProp)) {
    const found = allAgents.find((a) => a.name === nameFromProp);
    const foundDisplay = agentDisplayName(found);
    if (foundDisplay) return foundDisplay;
  }

  if (footerAgentId && !PSEUDO_AGENT_IDS.has(footerAgentId)) {
    const footerAgent = allAgents.find((a) => a.name === footerAgentId);
    const footerDisplay = agentDisplayName(footerAgent);
    if (footerDisplay) return footerDisplay;
  }

  return "EchoAI";
}

export function Welcome({
  className,
  agent,
  agentName,
}: {
  className?: string;
  agent?: Agent | null;
  agentName?: string | null;
}) {
  const { t } = useI18n();
  const [searchParams] = useSearchParams();
  const { agents: allAgents } = useAgents();
  const footerAgentId = useActiveAgentId();
  const isSkillSeed = searchParams.get("mode") === "skill";

  const greetingName = useMemo(
    () =>
      pickGreetingName(
        agent ?? null,
        agentName ?? null,
        allAgents,
        footerAgentId,
      ),
    [agent, agentName, allAgents, footerAgentId],
  );

  return (
    <div
      className={cn(
        "mx-auto flex w-full flex-col items-center justify-center px-5 pt-8 pb-6 text-center sm:px-8",
        className,
      )}
    >
      {isSkillSeed ? (
        <>
          <div className="flex flex-wrap items-center justify-center gap-x-2 gap-y-1 text-2xl font-semibold tracking-tight">
            {t.welcome.createYourOwnSkill}
          </div>
          <p className="max-w-xl text-muted-foreground/90 whitespace-pre-line text-sm leading-relaxed">
            {t.welcome.createYourOwnSkillDescription}
          </p>
        </>
      ) : (
        <h2 className="text-[28px] font-semibold tracking-tight text-foreground">
          {t.welcome.greeting.replace("{name}", greetingName)}
        </h2>
      )}
    </div>
  );
}
