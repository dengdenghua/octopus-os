import { memo } from "react";

import { BotIcon } from "lucide-react";

import { useI18n } from "@/core/i18n/hooks";
import { WorkstationSeat } from "../workstation-seat";
import type { AgentTile } from "../agent-workbench-utils";
import { repairMojibakeText } from "../agent-workbench-utils";
import type { AgentRunState } from "../agent-run-status";
import { agentRunDotClass } from "../agent-run-status";
import type { WorkbenchRosterSeat } from "./helpers";
import { dockAgentStatusLabel, rosterSeatRoleLabel } from "./helpers";

function MachineScopeRailImpl({
  agents,
  leaderSeat,
  mainRunState,
  rosterSeats,
  selectedAgentId,
  onSelectMain,
  onSelectAgent,
  onSelectRoster,
}: {
  agents: AgentTile[];
  leaderSeat: WorkbenchRosterSeat | null;
  mainRunState: AgentRunState;
  rosterSeats: WorkbenchRosterSeat[];
  selectedAgentId: string | null;
  onSelectMain: () => void;
  onSelectAgent: (agentId: string) => void;
  onSelectRoster: (seatId: string) => void;
}) {
  const { t } = useI18n();
  const hasCollaborators = rosterSeats.length > 0;
  const hasMachineChoices =
    Boolean(leaderSeat) || agents.length > 0 || hasCollaborators;
  if (!hasMachineChoices) return null;
  const mainSeatLabel =
    leaderSeat && hasCollaborators
      ? `${leaderSeat.name} · ${t.agentWorkbenchPanel.leaderSeat}`
      : leaderSeat?.name;
  const mainDockShowsPresence = Boolean(leaderSeat && hasCollaborators);
  return (
    <div
      className="flex min-w-0 shrink-0 items-center gap-2 border-t border-border-subtle bg-background/80 px-3 py-1.5"
      data-testid="workstation-bottom-rail"
    >
      <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        <WorkstationSeat
          name={leaderSeat?.name ?? t.agentWorkbenchPanel.mainController}
          avatar={leaderSeat?.icon ?? null}
          avatarUrl={leaderSeat?.avatarUrl ?? null}
          avatarNode={
            leaderSeat ? undefined : (
              <BotIcon
                className="size-3.5 text-muted-foreground"
                aria-hidden="true"
              />
            )
          }
          fallbackInitial={leaderSeat?.name.charAt(0)}
          selected={selectedAgentId === null}
          onClick={onSelectMain}
          dotClassName={
            mainDockShowsPresence
              ? "bg-success"
              : agentRunDotClass(mainRunState)
          }
          dotLabel={
            mainDockShowsPresence
              ? t.agentWorkbenchPanel.dockStatusPresent
              : undefined
          }
          ariaLabel={mainSeatLabel ?? t.agentWorkbenchPanel.viewMainAgentSlot}
          title={mainSeatLabel ?? t.agentWorkbenchPanel.mainAgentProcessTitle}
          iconOnly
          iconCaption={
            hasCollaborators ? t.agentWorkbenchPanel.leaderSeat : undefined
          }
          className="shrink-0"
        />
        {agents.map((agent) => {
          const label = agent.codename ?? agent.name ?? agent.label;
          return (
            <WorkstationSeat
              key={agent.id}
              name={repairMojibakeText(label)}
              avatar={agent.avatar}
              avatarNode={
                <BotIcon
                  className="size-3.5 text-muted-foreground"
                  aria-hidden="true"
                />
              }
              selected={selectedAgentId === agent.id}
              onClick={() => onSelectAgent(agent.id)}
              dotClassName={agentRunDotClass(agent.status)}
              dotLabel={dockAgentStatusLabel(agent.status, t)}
              ariaLabel={t.agentWorkbenchPanel.viewAgentProcess(label)}
              title={`${label}: ${agent.task}`}
              iconOnly
              className="shrink-0"
            />
          );
        })}
        {rosterSeats.map((seat) => {
          const roleLabel = rosterSeatRoleLabel(seat, t);
          return (
            <WorkstationSeat
              key={seat.id}
              name={seat.name}
              avatar={seat.icon ?? null}
              avatarUrl={seat.avatarUrl ?? null}
              showBotBadge
              fallbackInitial={seat.name.charAt(0)}
              dotClassName="bg-success"
              dotLabel={t.agentWorkbenchPanel.dockStatusPresent}
              title={`${seat.name} · ${roleLabel} · ${t.agentWorkbenchPanel.dockStatusPresent}`}
              ariaLabel={`${seat.name} · ${roleLabel} · ${t.agentWorkbenchPanel.dockStatusPresent}`}
              selected={selectedAgentId === seat.id}
              onClick={() => onSelectRoster(seat.id)}
              iconOnly
              className="shrink-0"
            />
          );
        })}
      </div>
    </div>
  );
}

export const MachineScopeRail = memo(MachineScopeRailImpl);
