import type { RecordingEvent } from "./types";

const INSTALL_AND_DRAIN = String.raw`(() => {
  const KEY = "__echoRecorderBrowserV1";
  const root = window;
  const privateSelector = [
    "input[type='password']",
    "[autocomplete*='one-time-code']",
    "[autocomplete^='cc-']",
    "[data-recorder-private='true']",
    "#echo-browser-control-indicator",
    "#echo-agent-cursor-overlay-host"
  ].join(",");
  const compact = (value, limit = 160) => String(value || "")
    .replace(/\s+/g, " ").trim().slice(0, limit);
  const targetInfo = (node) => ({
    tag: String(node.tagName || "").toLowerCase(),
    role: node.getAttribute?.("role") || undefined,
    id: node.id || undefined,
    name: node.getAttribute?.("name") || undefined,
    aria_label: node.getAttribute?.("aria-label") || undefined,
    test_id: node.getAttribute?.("data-testid") || undefined,
    text: compact(node.innerText || node.textContent),
  });
  const isSensitive = (node) => Boolean(
    node.matches?.(privateSelector) ||
    /password|passcode|secret|token|otp|card|cvv/i.test([
      node.id,
      node.getAttribute?.("name"),
      node.getAttribute?.("aria-label"),
      node.getAttribute?.("autocomplete")
    ].filter(Boolean).join(" "))
  );
  const selectorKey = (target) => [
    target.tag, target.id, target.name, target.aria_label, target.text
  ].filter(Boolean).join(":");

  if (!root[KEY]) {
    const state = { buffer: [], listeners: [] };
    const push = (event) => {
      const previous = state.buffer[state.buffer.length - 1];
      if (event.kind === "input" && previous?.kind === "input" &&
          previous?.data?.target_key === event?.data?.target_key) {
        state.buffer[state.buffer.length - 1] = event;
      } else {
        state.buffer.push(event);
      }
      if (state.buffer.length > 200) state.buffer.splice(0, state.buffer.length - 200);
    };
    const capture = (event) => {
      if (!event.isTrusted || !(event.target instanceof Element)) return;
      if (event.target.closest?.("[data-recorder-private='true']")) return;
      const node = event.target.closest?.(
        "button,a,input,textarea,select,[role],[contenteditable='true']"
      ) || event.target;
      if (!(node instanceof HTMLElement)) return;
      const target = targetInfo(node);
      const item = {
        ts: new Date().toISOString(),
        source: "browser",
        kind: event.type,
        app: "Browser",
        window: location.href,
        target,
        data: { title: document.title, target_key: selectorKey(target) },
      };
      if (event.type === "keydown") {
        const allowed = new Set([
          "Enter", "Escape", "Tab", "ArrowUp", "ArrowDown", "ArrowLeft",
          "ArrowRight", "Backspace", "Delete"
        ]);
        if (!allowed.has(event.key) && !event.metaKey && !event.ctrlKey && !event.altKey) return;
        item.data.key = event.key;
        item.data.meta = event.metaKey;
        item.data.ctrl = event.ctrlKey;
        item.data.alt = event.altKey;
        item.data.shift = event.shiftKey;
      } else if (event.type === "input" || event.type === "change") {
        const value = "value" in node ? String(node.value || "") : compact(node.textContent, 2000);
        item.data.value = isSensitive(node) ? "[REDACTED]" : value.slice(0, 2000);
        item.data.value_length = value.length;
        item.data.sensitive = isSensitive(node);
      }
      push(item);
    };
    ["pointerdown", "input", "change", "keydown"].forEach((type) => {
      document.addEventListener(type, capture, { capture: true, passive: type === "pointerdown" });
      state.listeners.push([type, capture]);
    });
    root[KEY] = state;
  }
  return root[KEY].buffer.splice(0, 100);
})()`;

/** Script installed in an Electron webview only while REC is active. */
export function browserRecorderDrainScript(): string {
  return INSTALL_AND_DRAIN;
}

export function normalizeBrowserRecordingEvents(
  value: unknown,
): RecordingEvent[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is RecordingEvent => {
    if (!item || typeof item !== "object") return false;
    const event = item as Partial<RecordingEvent>;
    return (
      typeof event.ts === "string" &&
      event.source === "browser" &&
      typeof event.kind === "string"
    );
  });
}
