/**
 * Public narrative projection for the streaming transcript.
 *
 * The realtime protocol is richer than what belongs in the main conversation.
 * This small, dependency-light projection keeps the transcript and Workbench
 * on the same public vocabulary without exposing tool names, raw arguments or
 * private reasoning.
 */
import { getActionDisplay, type ActionDisplay } from "./action-display";
import { extractFactSummary, type FactSummary } from "./fact-summary";

export type NarrativeKind =
  | "intent"
  | "thinking"
  | "action"
  | "fact"
  | "decision"
  | "answer";

export type NarrativeState =
  | "pending"
  | "running"
  | "waiting"
  | "done"
  | "error";

export type NarrativeEvidenceRef = {
  tab: ActionDisplay["workbenchTab"];
  eventId: string;
};

export type NarrativeBlock = {
  id: string;
  turnId?: string;
  phaseId?: string;
  kind: NarrativeKind;
  state: NarrativeState;
  title: string;
  object?: string;
  fact?: FactSummary | null;
  startedAt?: number;
  endedAt?: number;
  durationMs?: number;
  evidenceRefs: NarrativeEvidenceRef[];
  aggregateKey?: string;
};

export type ToolNarrativeInput = {
  id: string;
  toolName: string;
  args?: Record<string, unknown>;
  result?: unknown;
  phaseId?: string;
  turnId?: string;
  state?: NarrativeState;
  startedAt?: number;
  endedAt?: number;
};

export function projectToolNarrative(input: ToolNarrativeInput): NarrativeBlock {
  const display = getActionDisplay(input.toolName, input.args);
  const durationMs =
    input.startedAt !== undefined && input.endedAt !== undefined
      ? Math.max(0, input.endedAt - input.startedAt)
      : undefined;

  return {
    id: input.id,
    turnId: input.turnId,
    phaseId: input.phaseId,
    kind: "action",
    state: input.state ?? (input.result === undefined ? "running" : "done"),
    title: display.verb,
    object: display.object || undefined,
    fact: input.result === undefined
      ? null
      : extractFactSummary(input.toolName, input.result),
    startedAt: input.startedAt,
    endedAt: input.endedAt,
    durationMs,
    evidenceRefs: [{ tab: display.workbenchTab, eventId: input.id }],
    aggregateKey: `${input.phaseId ?? "none"}:${display.aggregateKind}`,
  };
}

export function narrativeDurationMs(block: NarrativeBlock, now = Date.now()): number | null {
  if (block.durationMs !== undefined) return block.durationMs;
  if (block.startedAt === undefined) return null;
  return Math.max(0, (block.endedAt ?? now) - block.startedAt);
}
