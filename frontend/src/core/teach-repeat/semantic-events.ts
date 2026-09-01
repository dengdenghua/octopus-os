import type { RecordingEvent } from "./types";

const PRIVATE_AUTOCOMPLETE = /password|one-time-code|cc-|transaction/i;
const CONTROL_KEYS = new Set([
  "Enter",
  "Escape",
  "Tab",
  "ArrowUp",
  "ArrowDown",
  "ArrowLeft",
  "ArrowRight",
  "Backspace",
  "Delete",
]);

function compactText(value: string | null | undefined, limit = 160): string {
  return (value ?? "").replace(/\s+/g, " ").trim().slice(0, limit);
}

function elementTarget(element: HTMLElement): Record<string, unknown> {
  return {
    tag: element.tagName.toLowerCase(),
    role: element.getAttribute("role") || undefined,
    id: element.id || undefined,
    name: element.getAttribute("name") || undefined,
    aria_label: element.getAttribute("aria-label") || undefined,
    test_id: element.getAttribute("data-testid") || undefined,
    text: compactText(element.innerText || element.textContent),
  };
}

function inputPayload(element: HTMLInputElement | HTMLTextAreaElement) {
  const autocomplete = element.autocomplete || "";
  const sensitive =
    (element instanceof HTMLInputElement && element.type === "password") ||
    PRIVATE_AUTOCOMPLETE.test(autocomplete) ||
    /password|passcode|secret|token|otp|card|cvv/i.test(
      `${element.name} ${element.id} ${element.getAttribute("aria-label") ?? ""}`,
    );
  return sensitive
    ? {
        value: "[REDACTED]",
        value_length: element.value.length,
        sensitive: true,
      }
    : {
        value: element.value.slice(0, 2000),
        value_length: element.value.length,
      };
}

/** Convert first-party DOM interaction into a portable semantic event. */
export function buildSemanticRecordingEvent(
  event: Event,
): RecordingEvent | null {
  const rawTarget = event.target;
  if (!(rawTarget instanceof HTMLElement)) return null;
  if (rawTarget.closest("[data-recorder-private='true']")) return null;

  const actionable =
    rawTarget.closest<HTMLElement>(
      "button,a,input,textarea,select,[role],[contenteditable='true']",
    ) ?? rawTarget;
  const base: RecordingEvent = {
    ts: new Date().toISOString(),
    source: "human",
    kind: event.type,
    app: "EchoAI",
    window: `${window.location.pathname}${window.location.hash}`,
    target: elementTarget(actionable),
  };

  if (event instanceof KeyboardEvent) {
    if (
      !CONTROL_KEYS.has(event.key) &&
      !event.metaKey &&
      !event.ctrlKey &&
      !event.altKey
    ) {
      return null;
    }
    base.data = {
      key: event.key,
      meta: event.metaKey,
      ctrl: event.ctrlKey,
      alt: event.altKey,
      shift: event.shiftKey,
    };
  } else if (
    event.type === "input" &&
    (actionable instanceof HTMLInputElement ||
      actionable instanceof HTMLTextAreaElement)
  ) {
    base.data = inputPayload(actionable);
  } else if (
    event.type === "change" &&
    actionable instanceof HTMLSelectElement
  ) {
    base.data = { value: actionable.value.slice(0, 500) };
  }

  return base;
}

export function recordingEventKey(event: RecordingEvent): string {
  const target = event.target ?? {};
  return [
    event.kind,
    String(target.id ?? ""),
    String(target.name ?? ""),
    String(target.aria_label ?? ""),
    String(target.tag ?? ""),
  ].join(":");
}
