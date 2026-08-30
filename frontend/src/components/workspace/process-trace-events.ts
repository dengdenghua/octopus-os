import type { LiveToolEvent } from "./live-tool-timeline";
import { toWorkBlocks } from "./work-blocks";

export function getProcessTraceEvents(
  events: LiveToolEvent[],
): LiveToolEvent[] {
  return toWorkBlocks(events).map((block) => block.event);
}

export function isAutoVerificationToolName(name: string): boolean {
  const normalized = name.trim().toLowerCase();
  const tail = normalized.split(":").pop() ?? normalized;
  return (
    tail === "verification" ||
    tail === "auto_verification" ||
    tail === "auto-verification"
  );
}

export function isCollapsibleAutoVerificationEvent(
  event: LiveToolEvent,
): boolean {
  return event.status === "done" && isAutoVerificationToolName(event.name);
}
