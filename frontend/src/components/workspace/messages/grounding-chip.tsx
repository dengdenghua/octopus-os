import type { Message } from "@/core/api/types";
import { BookOpenIcon, ChevronDownIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import {
  isPrivateAgentGroundingSource,
  type GroundingSource,
} from "@/core/realtime/items";
import { cn } from "@/lib/utils";

/**
 * Plain-language auto-prefetched project context chip.
 *
 * Surfaces the codebase grounding the agent actually used this turn — the wiki
 * pages + source chunks folded into its prompt — carried on the AI reply's
 * ``additional_kwargs.grounding``. Collapsed by default (one quiet line); click
 * to expand the exact docs/files. Builds trust that the answer is grounded in
 * the project without dumping retrieval noise into the chat.
 */
export function GroundingChip({ message }: { message: Message }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const sources = useMemo(() => {
    const g = message.additional_kwargs?.grounding;
    if (!Array.isArray(g) || g.length === 0) return null;
    return (g as GroundingSource[]).filter(
      (source) => !isPrivateAgentGroundingSource(source),
    );
  }, [message.additional_kwargs?.grounding]);
  if (!sources || sources.length === 0) return null;
  const firstSource = sources[0];
  const firstSourceLabel = firstSource?.title || firstSource?.path || "";
  const label = t.message.grounding.summary(firstSourceLabel, sources.length);
  return (
    <div
      className="mb-1"
      data-grounding-evidence="true"
      data-grounding-source-count={sources.length}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="inline-flex max-w-full items-center gap-1.5 bg-transparent px-0 py-0.5 text-xs leading-4 text-muted-foreground/55 transition-colors hover:text-muted-foreground"
      >
        <BookOpenIcon className="size-3 shrink-0 opacity-60" />
        <span className="truncate">{label}</span>
        <ChevronDownIcon
          className={cn(
            "size-2.5 shrink-0 opacity-50 transition-transform",
            open && "rotate-180",
          )}
        />
      </button>
      {open && (
        <ul className="mt-1 flex max-w-md flex-col gap-1 bg-transparent py-1 pl-4 text-xs leading-4">
          {sources.map((source, index) => (
            <li
              key={`${source.path}-${index}`}
              className="flex min-w-0 items-center gap-2"
            >
              <span className="truncate font-medium text-foreground/90">
                {source.title}
              </span>
              <span className="truncate text-muted-foreground/70">
                {source.path}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
