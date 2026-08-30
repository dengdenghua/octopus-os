import { isAIMessage, type Message } from "@/core/api/types";

import type { LiveToolEvent } from "./live-tool-timeline";

function todoItemsFromArgs(args: Record<string, unknown> | undefined) {
  const items = args?.items ?? args?.todos;
  return Array.isArray(items) ? items : null;
}

export function persistedTodoEventsFromMessages(
  messages: Message[],
): LiveToolEvent[] {
  const events: LiveToolEvent[] = [];

  messages.forEach((message, messageIndex) => {
    if (!isAIMessage(message)) return;

    message.tool_calls?.forEach((toolCall, toolIndex) => {
      if (toolCall.name !== "todo_write") return;
      const items = todoItemsFromArgs(toolCall.args);
      if (!items) return;

      const startedAt = messageIndex + toolIndex / 100;
      events.push({
        id:
          toolCall.id ??
          `persisted-todo:${message.id ?? messageIndex}:${toolIndex}`,
        name: "todo_write",
        status: "done",
        startedAt,
        finishedAt: startedAt,
        iteration: messageIndex + 1,
        input: {
          ...toolCall.args,
          items,
        },
        output: items,
      });
    });
  });

  return events;
}

export function latestPersistedTodoEventsFromMessages(
  messages: Message[],
): LiveToolEvent[] {
  for (
    let messageIndex = messages.length - 1;
    messageIndex >= 0;
    messageIndex -= 1
  ) {
    const message = messages[messageIndex];
    if (!message || !isAIMessage(message)) continue;
    const events: LiveToolEvent[] = [];
    message.tool_calls?.forEach((toolCall, toolIndex) => {
      if (toolCall.name !== "todo_write") return;
      const items = todoItemsFromArgs(toolCall.args);
      if (!items) return;
      const startedAt = messageIndex + toolIndex / 100;
      events.push({
        id:
          toolCall.id ??
          `persisted-todo:${message.id ?? messageIndex}:${toolIndex}`,
        name: "todo_write",
        status: "done",
        startedAt,
        finishedAt: startedAt,
        iteration: messageIndex + 1,
        input: {
          ...toolCall.args,
          items,
        },
        output: items,
      });
    });
    if (events.length > 0) return events;
  }
  return [];
}

export function restoredTodoEventsForDisplay({
  isLoading,
  lastTurnToolEvents,
  latestPersistedTodoEvents,
}: {
  isLoading: boolean;
  lastTurnToolEvents: LiveToolEvent[];
  latestPersistedTodoEvents: LiveToolEvent[];
}): LiveToolEvent[] {
  if (isLoading || lastTurnToolEvents.length > 0) return [];
  return latestPersistedTodoEvents;
}
