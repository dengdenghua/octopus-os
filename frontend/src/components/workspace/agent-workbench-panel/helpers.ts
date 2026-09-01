import type { Translations } from "@/core/i18n/locales/types";

import type { AgentTile, AgentWorkbenchTabId } from "../agent-workbench-utils";
import type { AgentPhase } from "../agent-phases";
import type { WorkBlock } from "../work-blocks";
import { workBlockLabelsFromShape } from "../work-blocks";

export type WorkbenchRosterSeat = {
  id: string;
  name: string;
  avatarUrl?: string | null;
  icon?: string | null;
  role?: "tl" | "member" | string | null;
  kind?: "human" | "agent" | "role";
};

export function rosterSeatRoleLabel(
  seat: WorkbenchRosterSeat,
  t: Translations,
): string {
  if (seat.role === "tl") return t.agentWorkbenchPanel.leaderSeat;
  if (seat.role && seat.role !== "member") return seat.role;
  return t.agentWorkbenchPanel.collaboratorSeat;
}

export function mainPhaseStatusLabel(phases: AgentPhase[], t: Translations) {
  if (phases.some((p) => p.status === "error")) {
    return t.agentWorkbenchPanel.agentStatusError;
  }
  if (phases.some((p) => p.status === "waiting_approval")) {
    return t.agentWorkbenchPages.statusWaitingApproval;
  }
  if (phases.some((p) => p.status === "running")) {
    return t.agentWorkbenchPanel.agentStatusRunning;
  }
  if (phases.some((p) => p.status === "pending")) {
    return t.agentWorkbenchPanel.agentStatusPending;
  }
  return t.agentWorkbenchPanel.agentStatusDone;
}

export function statusFromBlocks(blocks: WorkBlock[]): AgentPhase["status"] {
  if (blocks.some((block) => block.status === "waiting_approval")) {
    return "waiting_approval";
  }
  if (blocks.some((block) => block.status === "running")) {
    return "running";
  }
  if (blocks.some((block) => block.status === "error")) return "error";
  return blocks.length > 0 ? "done" : "pending";
}

export function dockAgentStatusLabel(
  status: AgentTile["status"],
  t: Translations,
): string {
  if (status === "running") return t.agentWorkbenchPanel.dockStatusRunning;
  if (status === "waiting_approval")
    return t.agentWorkbenchPages.statusWaitingApproval;
  if (status === "error") return t.agentWorkbenchPanel.dockStatusError;
  if (status === "done") return t.agentWorkbenchPanel.dockStatusDone;
  return t.agentWorkbenchPanel.dockStatusPending;
}

/**
 * The transcript is the narrative; the workbench is the proof surface. Map a
 * selected action to the one panel where it adds information instead of
 * opening a second copy of the activity log.
 */
export function evidenceTabForWorkBlock(block: WorkBlock): AgentWorkbenchTabId {
  if (block.kind === "terminal") return "terminal";
  if (block.kind === "browser") return "browser";

  const eventName = block.event.name.trim().toLowerCase();
  if (
    /(?:^|_)(?:write|edit|create|delete|rename|move|patch|replace)(?:_|$)/.test(
      eventName,
    )
  ) {
    return "diff";
  }

  return "agent";
}

export function workBlockLabelsFromI18n(t: unknown) {
  return workBlockLabelsFromShape(
    (t as { workBlocks?: unknown } | null)?.workBlocks,
  );
}

export function agentStatusTextClass(status: AgentTile["status"]): string {
  if (status === "running") return "text-primary";
  if (status === "waiting_approval") return "text-warning";
  if (status === "error") return "text-destructive";
  if (status === "done") return "text-success";
  return "text-muted-foreground";
}
