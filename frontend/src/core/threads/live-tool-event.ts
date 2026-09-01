/** Runtime tool activity projected for the Echo OS browser copilot. */
export interface LiveToolEvent {
  id: string;
  name: string;
  status: "running" | "done" | "error" | "waiting_approval";
  startedAt: number;
  durationMs?: number;
  finishedAt?: number;
  iteration: number;
  agentId?: string;
  agentName?: string;
  input?: Record<string, unknown>;
  output?: unknown;
  parentToolUseId?: string;
  subAgentRole?: string;
  subagentCodename?: string;
  subagentAvatar?: string;
  lifecycle?: "spawned" | "finished";
  iterationCount?: number;
  filesTouched?: string[];
  thought?: string;
  observation?: string;
}
