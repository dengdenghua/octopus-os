/**
 * Lightweight inline suggestion bar for intent-based mode auto-switching.
 *
 * Rendered above the composer when the intent classifier lands on a
 * "suggest" verdict (medium confidence) — either because the user set a
 * manual override (manual wins, auto only suggests) or because the signal
 * was too weak to auto-switch. Per user design preferences it stays quiet:
 * small muted text, no bubble shadow, flat background.
 *
 * Ignoring a suggestion for a given mode is remembered per-session so it
 * doesn't re-pop after every send.
 */

import { useCallback, useEffect, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import type { AgentModeName } from "../mode-selector";
import { cn } from "@/lib/utils";

const IGNORE_STORAGE_KEY = "echo:modeIntentDismissed";

interface ModeIntentSuggestionProps {
  /** The mode the classifier wants to switch to. */
  mode: AgentModeName;
  /** Human-readable label for the suggested mode. */
  modeLabel: string;
  /** Called when the user accepts the switch. */
  onAccept?: (mode: AgentModeName) => void;
  /** Called when the user dismisses the suggestion. */
  onDismiss?: (mode: AgentModeName) => void;
  className?: string;
}

/**
 * Return whether the given mode has been dismissed for this session, and a
 * setter to mark it dismissed. sessionStorage is intentionally used so a
 * refresh resets the ignore state.
 */
export function readDismissedModes(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.sessionStorage.getItem(IGNORE_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === "string")
      : [];
  } catch {
    return [];
  }
}

function writeDismissedModes(modes: string[]): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(IGNORE_STORAGE_KEY, JSON.stringify(modes));
  } catch {
    // Quota/private-mode failures shouldn't crash the composer.
  }
}

export function ModeIntentSuggestion({
  mode,
  modeLabel,
  onAccept,
  onDismiss,
  className,
}: ModeIntentSuggestionProps) {
  const { t } = useI18n();
  const [dismissed, setDismissed] = useState(() =>
    readDismissedModes().includes(mode),
  );

  // If the parent switches which mode it's suggesting (e.g. a new verdict
  // arrives), reset the local dismissed flag so the new suggestion shows.
  useEffect(() => {
    setDismissed(readDismissedModes().includes(mode));
  }, [mode]);

  const handleAccept = useCallback(() => {
    setDismissed(true);
    onAccept?.(mode);
  }, [mode, onAccept]);

  const handleDismiss = useCallback(() => {
    setDismissed(true);
    const next = readDismissedModes();
    if (!next.includes(mode)) {
      next.push(mode);
      writeDismissedModes(next);
    }
    onDismiss?.(mode);
  }, [mode, onDismiss]);

  if (dismissed) return null;

  return (
    <div
      data-testid="mode-intent-suggestion"
      className={cn(
        "flex items-center gap-2 px-1 pb-1 text-xs text-muted-foreground/75",
        className,
      )}
    >
      <span className="truncate">{t.modeIntent.suggestSwitch(modeLabel)}</span>
      <span className="ml-auto flex shrink-0 items-center gap-2">
        <button
          type="button"
          data-testid="mode-intent-accept"
          onClick={handleAccept}
          className="rounded-md px-2 py-0.5 font-medium text-primary transition-colors hover:bg-primary/10"
        >
          {t.modeIntent.switch}
        </button>
        <button
          type="button"
          data-testid="mode-intent-ignore"
          onClick={handleDismiss}
          className="rounded-md px-2 py-0.5 text-muted-foreground/70 transition-colors hover:bg-muted hover:text-foreground"
        >
          {t.modeIntent.ignore}
        </button>
      </span>
    </div>
  );
}
