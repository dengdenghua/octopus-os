import type { AgentPhase, AgentPhaseStatus } from "./agent-phases";
import type { LiveToolEvent } from "./live-tool-timeline";
import type { WorkBlock, WorkBlockStatus } from "./work-blocks";

export type AgentRunState =
  | "running"
  | "waiting"
  | "error"
  | "pending"
  | "done";

export type AgentRunStatusInput =
  | AgentRunState
  | AgentPhaseStatus
  | LiveToolEvent["status"]
  | WorkBlockStatus
  | "completed"
  | "waiting_approval";

export function agentRunStateFromStatus(
  status: AgentRunStatusInput,
): AgentRunState {
  if (status === "waiting" || status === "waiting_approval") {
    return "waiting";
  }
  if (status === "warning") return "done";
  if (status === "completed") return "done";
  return status;
}

export function workbenchRunState({
  blocks,
  paused,
  phases,
}: {
  blocks: WorkBlock[];
  paused?: boolean;
  phases: AgentPhase[];
}): AgentRunState {
  if (blocks.length === 0 && phases.length === 0) return "pending";
  if (
    blocks.some((block) => block.status === "error") ||
    phases.some((phase) => phase.status === "error")
  ) {
    return "error";
  }
  if (
    paused ||
    blocks.some((block) => block.status === "waiting_approval") ||
    phases.some((phase) => phase.status === "waiting_approval")
  ) {
    return "waiting";
  }
  if (
    blocks.some((block) => block.status === "running") ||
    phases.some((phase) => phase.status === "running")
  ) {
    return "running";
  }
  if (phases.some((phase) => phase.status === "pending")) {
    return "pending";
  }
  return "done";
}

export function agentRunDotClass(status: AgentRunStatusInput): string {
  const state = agentRunStateFromStatus(status);
  if (state === "error") return "bg-destructive";
  if (state === "running") return "bg-success";
  if (state === "waiting") return "bg-warning";
  if (state === "done") return "bg-muted-foreground/50";
  return "bg-warning/80";
}

export function agentRunStatusLightClass(
  status: AgentRunStatusInput,
): string {
  const state = agentRunStateFromStatus(status);
  if (state === "pending") return "bg-warning";
  return agentRunDotClass(state);
}

export function agentRunStatusLightPulseClass(
  status: AgentRunStatusInput,
): string | null {
  const state = agentRunStateFromStatus(status);
  if (state === "running") return "animate-ping";
  if (state === "waiting" || state === "pending") return "animate-pulse";
  return null;
}

export function agentRunProgressBarClass(status: AgentRunStatusInput): string {
  const state = agentRunStateFromStatus(status);
  if (state === "error") return "bg-destructive";
  if (state === "waiting") return "bg-warning";
  if (state === "pending") return "bg-warning/70";
  return "bg-success";
}

export function agentRunBadgeClass(status: AgentRunStatusInput): string {
  const state = agentRunStateFromStatus(status);
  if (state === "running") {
    return "bg-success/10 text-success";
  }
  if (state === "waiting") {
    return "bg-warning/10 text-warning";
  }
  if (state === "pending") {
    return "bg-warning/10 text-warning";
  }
  if (state === "error") return "bg-destructive/10 text-destructive";
  return "bg-muted text-muted-foreground";
}

export function agentRunTextClass(status: AgentRunStatusInput): string {
  const state = agentRunStateFromStatus(status);
  if (state === "running") return "text-foreground";
  if (state === "waiting" || state === "pending")
    return "text-warning";
  if (state === "done") return "text-success";
  if (state === "error") return "text-destructive";
  return "text-muted-foreground";
}

export function agentRunIconClass(status: AgentRunStatusInput): string {
  const state = agentRunStateFromStatus(status);
  if (state === "running") return "text-success";
  if (state === "waiting" || state === "pending")
    return "text-warning";
  if (state === "error") return "text-destructive";
  if (state === "done") return "text-info dark:text-info";
  return "text-muted-foreground";
}

export function agentRunPanelClass(status: AgentRunStatusInput): string {
  const state = agentRunStateFromStatus(status);
  if (state === "running") {
    return "border-success/25 bg-success/10 text-success";
  }
  if (state === "waiting") {
    return "border-warning/30 bg-warning/10 text-warning";
  }
  if (state === "pending") {
    return "border-warning/30 bg-warning/10 text-warning";
  }
  if (state === "error") {
    return "border-destructive/35 bg-destructive/10 text-destructive";
  }
  if (state === "done") {
    return "border-info/25 bg-info/10 text-info dark:text-info";
  }
  return "border-border-default bg-muted/45 text-muted-foreground";
}

export function agentRunRobotButtonClass(status: AgentRunStatusInput): string {
  const state = agentRunStateFromStatus(status);
  if (state === "running") {
    return "border-success/40 bg-success/10 animate-pulse";
  }
  if (state === "waiting") return "border-warning/40 bg-warning/10";
  if (state === "pending") return "border-warning/40 bg-warning/10";
  if (state === "error") return "border-destructive/50 bg-destructive/10";
  if (state === "done") return "border-info/30 bg-info/8";
  return "border-border-default bg-muted/35";
}

export function agentRunAvatarAnimationClass(
  status: AgentRunStatusInput,
): string | null {
  const state = agentRunStateFromStatus(status);
  if (state === "running" || state === "waiting" || state === "pending") {
    return "animate-pulse";
  }
  return null;
}

export function agentRunHue(status: AgentRunStatusInput): number {
  const state = agentRunStateFromStatus(status);
  if (state === "error") return 8;
  if (state === "waiting" || state === "pending") return 42;
  return 118;
}

export function agentRunBeadTone({
  paused,
  runFailed,
  status,
  waiting,
}: {
  paused?: boolean;
  runFailed?: boolean;
  status: AgentRunStatusInput;
  waiting?: boolean;
}): { bead: string; halo: string | null } {
  const state =
    runFailed || agentRunStateFromStatus(status) === "error"
      ? "error"
      : paused || waiting
        ? "waiting"
        : agentRunStateFromStatus(status);
  if (state === "error") {
    return {
      bead: "bg-destructive/75 shadow-destructive/15",
      halo: null,
    };
  }
  if (state === "waiting") {
    return {
      bead: "bg-warning/70 shadow-warning/15",
      halo: null,
    };
  }
  if (state === "running") {
    return {
      bead: "bg-success/70 shadow-success/15",
      halo: "bg-success/15 animate-pulse",
    };
  }
  return {
    bead: "bg-muted-foreground/45 shadow-black/10",
    halo: null,
  };
}
