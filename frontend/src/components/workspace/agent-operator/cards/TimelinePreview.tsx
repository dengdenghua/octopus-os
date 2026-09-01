import { Badge } from "@/components/ui/badge";
import type { AgentTraceProcessTimeline } from "@/core/agent-trace/api";
import { Clock3Icon } from "lucide-react";
import { EmptyPanel } from "../operator-primitives";
import { formatScore } from "../operator-utils";
import { useOperatorCopy } from "../use-operator-copy";

export function TimelinePreview({
  timeline,
}: {
  timeline: AgentTraceProcessTimeline | null;
}) {
  const to = useOperatorCopy();
  if (!timeline) {
    return <EmptyPanel title={to("No process timeline available")} />;
  }
  const nodes = timeline.timeline.slice(0, 8);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <Badge variant="outline" className="text-xs">
          {to("score")} {formatScore(timeline.overview.score)}
        </Badge>
        <Badge variant="outline" className="text-xs">
          {to("approvals")} {timeline.overview.approval_count ?? 0}
        </Badge>
        <Badge variant="outline" className="text-xs">
          {to("lessons")} {timeline.overview.experience_record_count ?? 0}
        </Badge>
      </div>
      <div className="max-h-72 space-y-1 overflow-y-auto pr-1">
        {nodes.map((node, index) => (
          <div
            key={`${node.lane}-${node.kind}-${node.ts ?? index}`}
            className="grid grid-cols-[5.5rem_1fr] gap-2 rounded-md bg-background/55 px-2 py-1.5 text-xs"
          >
            <div className="flex items-center gap-1.5 text-muted-foreground">
              <Clock3Icon className="size-3" />
              <span className="truncate">{node.lane}</span>
            </div>
            <div className="min-w-0">
              <div className="truncate font-medium">
                {node.title || node.kind}
              </div>
              {(node.text || node.tool || node.status) && (
                <div className="truncate text-muted-foreground">
                  {node.text || node.tool || node.status}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
