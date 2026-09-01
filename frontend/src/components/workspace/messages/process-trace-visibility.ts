import type { LiveToolEvent } from "../live-tool-timeline";
import { getProcessTraceEvents } from "../process-trace-events";

export type ProcessTraceMode =
  | "chat"
  | "flash"
  | "thinking"
  | "react"
  | "deep"
  | "team"
  | "code";

function isActive(event: LiveToolEvent) {
  return event.status === "running" || event.status === "waiting_approval";
}

export function shouldShowProcessTrace(
  events: LiveToolEvent[] | undefined,
  hasAnswer = false,
  mode: ProcessTraceMode = "react",
) {
  const topLevel = getProcessTraceEvents(events ?? []);
  if (topLevel.length === 0) return false;
  if (isPlainChatMode(mode)) {
    if (hasAnswer) return false;
    return topLevel.some(isActive) || topLevel.some(isError);
  }

  return true;
}

export function shouldOpenProcessTraceByDefault(
  events: LiveToolEvent[] | undefined,
  hasAnswer = false,
  mode: ProcessTraceMode = "react",
) {
  const topLevel = getProcessTraceEvents(events ?? []);
  if (topLevel.length === 0) return false;
  if (isPlainChatMode(mode)) {
    return !hasAnswer && (topLevel.some(isActive) || topLevel.some(isError));
  }
  if (topLevel.some(isActive)) return true;
  if (hasAnswer) return false;
  if (topLevel.some(isError)) return true;
  return false;
}

function isPlainChatMode(mode: ProcessTraceMode) {
  return mode === "chat" || mode === "flash" || mode === "thinking";
}

function isError(event: LiveToolEvent) {
  return event.status === "error";
}
