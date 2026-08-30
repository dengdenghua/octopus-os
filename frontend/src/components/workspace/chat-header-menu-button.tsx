import { HistoryIcon } from "lucide-react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

/** Opens the searchable conversation-history drawer from the chat header. */
export function ChatHeaderMenuButton({
  onClick,
  className,
}: {
  onClick: () => void;
  className?: string;
}) {
  const { t } = useI18n();
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={t.codeMode.threadsHistory}
      title={t.codeMode.threadsHistory}
      className={cn(
        "flex size-[42px] shrink-0 items-center justify-center rounded-lg text-muted-foreground sm:size-8",
        "transition-colors duration-base hover:bg-muted hover:text-foreground",
        "outline-none focus-visible:ring-2 focus-visible:ring-ring/30",
        className,
      )}
    >
      <HistoryIcon className="size-4" strokeWidth={2} />
    </button>
  );
}
