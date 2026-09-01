/**
 * FinalArtifactCompletionNotice — extracted from `workspace/realtime/[thread_id]/page.tsx`
 * (P3 decomposition). Behavior-preserving move.
 */
import { FileTextIcon } from "lucide-react";

import type { DiffEntry } from "@/components/workspace/agent-workbench-utils";
import { useI18n } from "@/core/i18n/hooks";

export function FinalArtifactCompletionNotice({
  entries,
  onOpen,
}: {
  entries: DiffEntry[];
  onOpen: () => void;
}) {
  const { t } = useI18n();
  const first = entries[0];
  if (!first) return null;
  const extraCount = Math.max(0, entries.length - 1);
  return (
    <button
      type="button"
      onClick={onOpen}
      className="my-2 ml-11 flex max-w-full items-center gap-2 rounded-md border border-success/25 bg-success/10 px-3 py-2 text-left text-xs text-success transition-colors hover:bg-success/15"
    >
      <FileTextIcon className="size-4 shrink-0" />
      <span className="min-w-0 flex-1">
        <span className="font-medium">
          {t.realtime.finalArtifact.generated}
        </span>
        <span className="ml-2 font-mono text-xs text-success/80">
          {first.path || first.title}
        </span>
        {extraCount > 0 && (
          <span className="ml-2 text-success/80">+{extraCount}</span>
        )}
      </span>
      <span className="shrink-0 text-xs text-success/75">
        {t.realtime.finalArtifact.view}
      </span>
    </button>
  );
}
