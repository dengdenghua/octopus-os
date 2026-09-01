import type { BaseStream } from "@/core/api/use-stream-types";
import { useEffect } from "react";

import { useI18n } from "@/core/i18n/hooks";
import type { AgentThreadState } from "@/core/threads";
import { cn } from "@/lib/utils";

import { useThreadChat } from "./chats";
import { FlipDisplay } from "./flip-display";

export function ThreadTitle({
  className,
  threadId,
  thread,
  title,
}: {
  className?: string;
  threadId: string;
  thread: BaseStream<AgentThreadState>;
  /** Already-resolved thread title (from metadata / first user message).
   * The realtime stream state never carries a ``title`` (the adapter maps
   * the live turn stream, which has no title field), so without this the
   * browser tab falls back to the generic "untitled" even when the thread
   * has a proper derived title. */
  title?: string;
}) {
  const { t } = useI18n();
  const { isNewThread } = useThreadChat();
  const resolvedTitle = title || thread.values?.title || "";
  useEffect(() => {
    let _title = resolvedTitle;
    if (!_title) {
      _title = isNewThread ? t.pages.newChat : t.pages.untitled;
    }
    if (thread.isThreadLoading) {
      document.title = `Loading... - ${t.pages.appName}`;
    } else {
      document.title = `${_title} - ${t.pages.appName}`;
    }
  }, [
    resolvedTitle,
    isNewThread,
    t.pages.newChat,
    t.pages.untitled,
    t.pages.appName,
    thread.isThreadLoading,
  ]);

  // Fallback: show a muted placeholder on new/untitled threads so the
  // header has an anchor on the left side (otherwise justify-between
  // throws the right-side actions hard to one edge and the header looks
  // Implementation note.
  const fallback = resolvedTitle
    ? null
    : isNewThread
      ? t.pages.newChat
      : t.pages.untitled;
  const displayTitle = resolvedTitle || fallback;
  if (!displayTitle) return null;
  const isPlaceholder = !resolvedTitle;
  return (
    <FlipDisplay
      uniqueKey={threadId}
      className={cn(
        "max-w-[min(48vw,38rem)] truncate text-sm font-medium",
        isPlaceholder
          ? "text-muted-foreground/70 italic"
          : "text-foreground/80",
        className,
      )}
    >
      {displayTitle}
    </FlipDisplay>
  );
}
