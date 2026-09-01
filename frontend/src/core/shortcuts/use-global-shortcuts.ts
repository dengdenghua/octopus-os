import { useEffect } from "react";
import {
  emitToggleSidebar,
  emitCommandPalette,
  emitFileSearch,
  emitGoToLine,
  emitTogglePanel,
} from "../events";

/**
 * Registers workspace-level keyboard shortcuts that emit events via EventBus.
 *
 * Components opt-in to these events via `useEvent` hook so the
 * shortcut layer stays decoupled from individual UI state.
 */
export function useWorkspaceShortcuts() {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;

      // Ctrl+B: Toggle sidebar
      if (mod && e.key === "b") {
        e.preventDefault();
        emitToggleSidebar();
      }

      // Ctrl+J: Focus chat input
      if (mod && e.key === "j") {
        e.preventDefault();
        document
          .querySelector<HTMLTextAreaElement>("[data-chat-input]")
          ?.focus();
      }

      // Ctrl+Shift+P: Command palette (alternative to Ctrl+K)
      if (mod && e.shiftKey && e.key === "P") {
        e.preventDefault();
        emitCommandPalette();
      }

      // Ctrl+P: file quick open (code mode)
      if (mod && !e.shiftKey && e.key.toLowerCase() === "p") {
        e.preventDefault();
        emitFileSearch();
      }

      // Ctrl+G: go to line (code mode)
      if (mod && !e.shiftKey && e.key.toLowerCase() === "g") {
        e.preventDefault();
        emitGoToLine(1);
      }

      // Ctrl+\: Toggle right panel (code mode)
      if (mod && e.key === "\\") {
        e.preventDefault();
        emitTogglePanel();
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);
}
