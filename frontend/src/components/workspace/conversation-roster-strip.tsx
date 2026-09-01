import { useMemo } from "react";

import type { WorkbenchRosterSeat } from "./agent-workbench-panel";
import { rosterSeatRoleLabel } from "./agent-workbench-panel/helpers";
import { WorkstationSeat } from "./workstation-seat";

import { useI18n } from "@/core/i18n/hooks";

function isLeader(seat: WorkbenchRosterSeat) {
  const role = seat.role?.trim().toLowerCase();
  return role === "tl" || role === "owner" || seat.role === "群主";
}

export function ConversationRosterStrip({
  seats,
  onMemberClick,
}: {
  seats: WorkbenchRosterSeat[];
  onMemberClick?: (seat: WorkbenchRosterSeat) => void;
}) {
  const { t } = useI18n();
  const orderedSeats = useMemo(() => {
    const unique = Array.from(
      new Map(
        seats
          .filter((seat) => seat.id.trim() && seat.kind !== "human")
          .map((seat) => [seat.id, seat]),
      ).values(),
    );
    return unique.sort(
      (left, right) => Number(isLeader(right)) - Number(isLeader(left)),
    );
  }, [seats]);

  if (orderedSeats.length === 0) return null;

  return (
    <div
      className="flex min-w-0 shrink-0 items-center gap-1.5"
      data-testid="conversation-roster-strip"
      aria-label={t.chatInputBox.responseMode}
    >
      {orderedSeats.map((seat) => {
        const leader = isLeader(seat);
        const roleLabel = rosterSeatRoleLabel(seat, t);
        const label = `${seat.name} · ${roleLabel} · ${t.agentWorkbenchPanel.dockStatusPresent}`;
        return (
          <WorkstationSeat
            key={seat.id}
            name={seat.name}
            avatar={seat.icon ?? null}
            avatarUrl={seat.avatarUrl ?? null}
            showBotBadge={seat.kind === "agent" && !leader}
            fallbackInitial={seat.name.charAt(0)}
            dotClassName="bg-success"
            dotLabel={t.agentWorkbenchPanel.dockStatusPresent}
            title={label}
            ariaLabel={label}
            onClick={onMemberClick ? () => onMemberClick(seat) : undefined}
            iconOnly
            iconCaption={leader ? "★" : undefined}
            className="shrink-0"
          />
        );
      })}
    </div>
  );
}
