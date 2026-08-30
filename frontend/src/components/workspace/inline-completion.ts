import {
  EditorView,
  Decoration,
  type DecorationSet,
  ViewPlugin,
  type ViewUpdate,
  WidgetType,
  keymap,
} from "@codemirror/view";
import { StateField, StateEffect, type Extension } from "@codemirror/state";
import { swallow } from "@/core/utils/log";
import { getBackendBaseURL } from "@/core/config";
import { authHeaders } from "@/core/auth/api";

const setGhostText = StateEffect.define<string>();
const clearGhostText = StateEffect.define<void>();

class GhostTextWidget extends WidgetType {
  constructor(readonly text: string) {
    super();
  }
  toDOM() {
    const span = document.createElement("span");
    span.className = "cm-ghost-text";
    span.textContent = this.text;
    span.style.opacity = "0.4";
    span.style.fontStyle = "italic";
    span.style.pointerEvents = "none";
    return span;
  }
  eq(other: GhostTextWidget) {
    return this.text === other.text;
  }
}

const ghostField = StateField.define<DecorationSet>({
  create() {
    return Decoration.none;
  },
  update(deco, tr) {
    for (const e of tr.effects) {
      if (e.is(setGhostText) && e.value) {
        const pos = tr.state.selection.main.head;
        const widget = Decoration.widget({
          widget: new GhostTextWidget(e.value),
          side: 1,
        });
        return Decoration.set([widget.range(pos)]);
      }
      if (e.is(clearGhostText)) return Decoration.none;
    }
    if (tr.docChanged || tr.selection) return Decoration.none;
    return deco;
  },
  provide: (f) => EditorView.decorations.from(f),
});

let pendingTimer: ReturnType<typeof setTimeout> | null = null;
let abortController: AbortController | null = null;
let lastGhostText = "";

async function fetchCompletion(
  prefix: string,
  suffix: string,
  filePath: string,
  signal: AbortSignal,
): Promise<string> {
  const res = await fetch(`${getBackendBaseURL()}/api/complete`, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ prefix, suffix, file_path: filePath }),
    signal,
  });
  if (!res.ok) return "";
  const data = await res.json();
  return data.completion ?? "";
}

const completionPlugin = (filePath: string) =>
  ViewPlugin.fromClass(
    class {
      update(update: ViewUpdate) {
        if (!update.docChanged) return;
        if (pendingTimer) clearTimeout(pendingTimer);
        if (abortController) abortController.abort();
        lastGhostText = "";
        update.view.dispatch({ effects: clearGhostText.of(undefined) });

        pendingTimer = setTimeout(() => {
          const state = update.view.state;
          const pos = state.selection.main.head;
          const doc = state.doc.toString();
          const prefix = doc.slice(0, pos);
          const suffix = doc.slice(pos);

          if (prefix.length < 5) return;

          abortController = new AbortController();
          fetchCompletion(prefix, suffix, filePath, abortController.signal)
            .then((text) => {
              if (text && update.view.state.selection.main.head === pos) {
                lastGhostText = text;
                update.view.dispatch({ effects: setGhostText.of(text) });
              }
            })
            .catch((e) => {
              swallow(e);
            });
        }, 500);
      }
    },
  );

const acceptGhostKeymap = keymap.of([
  {
    key: "Tab",
    run(view) {
      if (!lastGhostText) return false;
      const pos = view.state.selection.main.head;
      view.dispatch({
        changes: { from: pos, insert: lastGhostText },
        effects: clearGhostText.of(undefined),
      });
      lastGhostText = "";
      return true;
    },
  },
  {
    key: "Escape",
    run(view) {
      if (!lastGhostText) return false;
      view.dispatch({ effects: clearGhostText.of(undefined) });
      lastGhostText = "";
      return true;
    },
  },
]);

export function inlineCompletion(filePath: string): Extension {
  return [ghostField, completionPlugin(filePath), acceptGhostKeymap];
}
