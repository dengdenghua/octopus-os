import { Badge } from "@/components/ui/badge";
import type { AgentTraceExperienceQualitySummary } from "@/core/agent-trace/api";
import { cn } from "@/lib/utils";
import { GateStat } from "../../replay-panel";
import { GitBranchIcon } from "lucide-react";
import { useOperatorCopy } from "../use-operator-copy";

export function MemoryQualityCard({
  summary,
}: {
  summary: AgentTraceExperienceQualitySummary;
}) {
  const to = useOperatorCopy();
  const risky =
    summary.contradicted_count > 0 ||
    summary.stale_count > 0 ||
    summary.low_reliability_count > 0;
  const reliabilityPercent = Math.round((summary.avg_reliability ?? 0) * 100);
  const topAction = summary.next_actions[0];
  return (
    <div
      className={cn(
        "mt-3 rounded-lg border px-3 py-2",
        risky
          ? "border-warning/30 bg-warning/10"
          : summary.total > 0
            ? "border-success/25 bg-success/10"
            : "border-border-default bg-muted/15",
      )}
    >
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            <GitBranchIcon className="size-4 text-primary" />
            {to("Memory quality")}
            <Badge variant="outline" className="text-xs">
              {reliabilityPercent}% {to("reliable")}
            </Badge>
          </div>
          <div className="mt-1 truncate text-xs text-muted-foreground">
            {topAction ??
              (summary.total > 0
                ? to("Recall memories are fresh and contradiction-clean")
                : to("No committed experience memories yet"))}
          </div>
          <div className="mt-1 truncate font-mono text-xs text-muted-foreground">
            {to("active")} {summary.active_count} · {to("bucket experience")}{" "}
            {summary.by_bucket.experience ?? 0}
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-right font-mono text-xs">
          <GateStat label={to("mem")} value={summary.total} />
          <GateStat label={to("stale")} value={summary.stale_count} />
          <GateStat label={to("contra")} value={summary.contradicted_count} />
          <GateStat label={to("low")} value={summary.low_reliability_count} />
        </div>
      </div>
    </div>
  );
}
