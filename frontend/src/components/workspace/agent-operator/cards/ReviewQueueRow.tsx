import { Badge } from "@/components/ui/badge";
import type { AgentTraceReviewQueueItem } from "@/core/agent-trace/api";
import { cn } from "@/lib/utils";
import { priorityClass } from "../../replay-panel";
import { ArchiveIcon, CheckCircle2Icon, XCircleIcon } from "lucide-react";
import { IconButton } from "../operator-primitives";
import { shortId } from "../operator-utils";
import { useOperatorCopy } from "../use-operator-copy";

export function ReviewQueueRow({
  item,
  busy,
  onPromote,
  onReject,
  onArchive,
}: {
  item: AgentTraceReviewQueueItem;
  busy: boolean;
  onPromote: () => void;
  onReject: () => void;
  onArchive: () => void;
}) {
  const to = useOperatorCopy();
  return (
    <div className="rounded-lg border border-border-default bg-background/65 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge className={cn("text-xs", priorityClass(item.priority))}>
              {item.priority}
            </Badge>
            <Badge variant="outline" className="text-xs">
              {item.target_bucket}
            </Badge>
            <span className="text-xs text-muted-foreground">
              x{item.occurrences}
            </span>
          </div>
          <div className="mt-2 text-sm font-medium">{item.title}</div>
          <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
            {item.text}
          </p>
          <div className="mt-2 flex flex-wrap gap-2 text-xs text-muted-foreground">
            <span>{item.candidate_kind}</span>
            {(item.source_task_ids ?? []).slice(0, 2).map((taskId) => (
              <span key={taskId} className="font-mono">
                {shortId(taskId)}
              </span>
            ))}
          </div>
        </div>
        <div className="flex shrink-0 gap-1">
          <IconButton
            label={to("Promote")}
            disabled={busy}
            onClick={onPromote}
            icon={<CheckCircle2Icon className="size-3.5" />}
          />
          <IconButton
            label={to("Reject")}
            disabled={busy}
            onClick={onReject}
            icon={<XCircleIcon className="size-3.5" />}
          />
          <IconButton
            label={to("Archive")}
            disabled={busy}
            onClick={onArchive}
            icon={<ArchiveIcon className="size-3.5" />}
          />
        </div>
      </div>
    </div>
  );
}
