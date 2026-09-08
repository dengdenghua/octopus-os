import { useSearchParams } from "react-router-dom";
import { useMemo } from "react";

import { type Agent, useAgents } from "@/core/agents";
import { useActiveAgentId } from "@/core/agents/active";
import { getAssistantDisplayName } from "@/core/agents/assistant-naming";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

/** Pseudo agent IDs used in URLs that are not real agent names. */
const PSEUDO_AGENT_IDS = new Set(["", "new", "echo-assistant"]);

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
  const resolvedId = agentNameProp?.trim() || agentProp?.name || footerAgentId;
  if (resolvedId === "echo") return getAssistantDisplayName();
  const propDisplay =
    agentProp?.name === resolvedId ? agentDisplayName(agentProp) : null;
  if (propDisplay) return propDisplay;
  if (resolvedId && !PSEUDO_AGENT_IDS.has(resolvedId)) {
    const found = allAgents.find((agent) => agent.name === resolvedId);
    const foundDisplay = agentDisplayName(found);
    return foundDisplay || resolvedId;
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
