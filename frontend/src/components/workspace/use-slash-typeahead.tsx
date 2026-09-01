/* Implementation note. */
import {
  type KeyboardEvent,
  type ReactNode,
  useCallback,
  useMemo,
  useState,
} from "react";

import type { SlashCommand } from "@/core/slash-commands/api";
import { SlashCommandPicker } from "./slash-command-picker";

export interface UseSlashTypeaheadArgs {
  draft: string;
  setDraft: (value: string) => void;
  /** Called after a pick so the composer can re-focus its textarea. */
  focusTextarea?: () => void;
}

export interface UseSlashTypeaheadResult {
  /** JSX to render above the textarea (absolute-positioned dropdown). */
  picker: ReactNode;
  /** Wire this into the textarea's onKeyDown · returns true when consumed. */
  handleKeyDown: (e: KeyboardEvent<HTMLTextAreaElement>) => boolean;
  /** True iff the picker is currently visible. Consumers may use this
   *  to suppress their own Enter-to-submit when the user is navigating
   *  the typeahead. */
  slashOpen: boolean;
}

export function useSlashTypeahead({
  draft,
  setDraft,
  focusTextarea,
}: UseSlashTypeaheadArgs): UseSlashTypeaheadResult {
  // Typeahead is active when the draft starts with "/" and the cursor
  // is still inside the command token (no space typed yet).
  const slashToken = useMemo(() => {
    const trimmed = draft.trimStart();
    if (!trimmed.startsWith("/")) return null;
    const firstSpace = trimmed.indexOf(" ");
    return firstSpace === -1 ? trimmed.slice(1) : null;
  }, [draft]);
  const slashOpen = slashToken !== null;

  const [slashIdx, setSlashIdx] = useState(0);
  const [slashMatches, setSlashMatches] = useState<SlashCommand[]>([]);

  const pickSlash = useCallback(
    (cmd: SlashCommand) => {
      setDraft(`/${cmd.name} `);
      setSlashIdx(0);
      queueMicrotask(() => focusTextarea?.());
    },
    [setDraft, focusTextarea],
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>): boolean => {
      if (!slashOpen || slashMatches.length === 0) return false;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSlashIdx((i) => (i + 1) % slashMatches.length);
        return true;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSlashIdx((i) => (i - 1 + slashMatches.length) % slashMatches.length);
        return true;
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault();
        const chosen = slashMatches[slashIdx];
        if (chosen) pickSlash(chosen);
        return true;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        // Drop the leading `/` to close the picker without losing args
        setDraft(draft.replace(/^\s*\//, ""));
        return true;
      }
      return false;
    },
    [slashOpen, slashMatches, slashIdx, pickSlash, draft, setDraft],
  );

  const picker = slashOpen ? (
    <SlashCommandPicker
      filter={slashToken ?? ""}
      activeIndex={slashIdx}
      onPick={pickSlash}
      onMatchesChange={(m) => {
        setSlashMatches(m);
        setSlashIdx((i) => (m.length ? Math.min(i, m.length - 1) : 0));
      }}
    />
  ) : null;

  return { picker, handleKeyDown, slashOpen };
}
