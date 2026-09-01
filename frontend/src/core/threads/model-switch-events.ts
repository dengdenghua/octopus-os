const MODEL_SWITCH_EVENT_PREFIX = "echo:model-switch-events:";
const MODEL_SWITCH_EVENT_VERSION = 1;
const MODEL_SWITCH_EVENT_LIMIT = 80;
const MODEL_SWITCH_EVENT_MAX_AGE_MS = 90 * 24 * 60 * 60 * 1000;

export interface ModelSwitchEvent {
  id: string;
  modelName: string;
  createdAt: string;
  /** Number of persisted/streaming messages visible when the switch happened. */
  afterMessageCount: number;
}

interface ModelSwitchEventEnvelope {
  v: 1;
  events: ModelSwitchEvent[];
}

function storageKey(threadId: string): string {
  return `${MODEL_SWITCH_EVENT_PREFIX}${threadId.trim()}`;
}

function isModelSwitchEvent(value: unknown): value is ModelSwitchEvent {
  if (!value || typeof value !== "object") return false;
  const event = value as Partial<ModelSwitchEvent>;
  return (
    typeof event.id === "string" &&
    event.id.length > 0 &&
    typeof event.modelName === "string" &&
    event.modelName.trim().length > 0 &&
    typeof event.createdAt === "string" &&
    Number.isFinite(Date.parse(event.createdAt)) &&
    typeof event.afterMessageCount === "number" &&
    Number.isInteger(event.afterMessageCount) &&
    event.afterMessageCount >= 0
  );
}

function currentStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function loadModelSwitchEvents(
  threadId: string,
  now = Date.now(),
): ModelSwitchEvent[] {
  const normalizedThreadId = threadId.trim();
  const storage = currentStorage();
  if (!normalizedThreadId || !storage) return [];
  try {
    const raw = storage.getItem(storageKey(normalizedThreadId));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as Partial<ModelSwitchEventEnvelope>;
    if (
      parsed.v !== MODEL_SWITCH_EVENT_VERSION ||
      !Array.isArray(parsed.events)
    ) {
      return [];
    }
    return parsed.events
      .filter(isModelSwitchEvent)
      .filter(
        (event) =>
          now - Date.parse(event.createdAt) <= MODEL_SWITCH_EVENT_MAX_AGE_MS,
      )
      .slice(-MODEL_SWITCH_EVENT_LIMIT);
  } catch {
    return [];
  }
}

export function recordModelSwitchEvent(
  threadId: string,
  currentEvents: readonly ModelSwitchEvent[],
  input: {
    modelName: string;
    afterMessageCount: number;
    now?: number;
  },
): ModelSwitchEvent[] {
  const normalizedThreadId = threadId.trim();
  const modelName = input.modelName.trim();
  if (!normalizedThreadId || !modelName) return [...currentEvents];

  const now = input.now ?? Date.now();
  const event: ModelSwitchEvent = {
    id: `model-switch:${now}:${input.afterMessageCount}`,
    modelName,
    createdAt: new Date(now).toISOString(),
    afterMessageCount: Math.max(0, Math.trunc(input.afterMessageCount)),
  };
  const next = [...currentEvents];
  const previous = next[next.length - 1];
  // Switching A → B → C before sending a message affects only the next turn.
  // Keep one truthful marker instead of filling the timeline with picker taps.
  if (previous?.afterMessageCount === event.afterMessageCount) {
    next[next.length - 1] = event;
  } else {
    next.push(event);
  }
  const bounded = next.slice(-MODEL_SWITCH_EVENT_LIMIT);
  const storage = currentStorage();
  if (storage) {
    try {
      const envelope: ModelSwitchEventEnvelope = { v: 1, events: bounded };
      storage.setItem(storageKey(normalizedThreadId), JSON.stringify(envelope));
    } catch {
      // Restricted/private browser storage must not break model switching.
    }
  }
  return bounded;
}
