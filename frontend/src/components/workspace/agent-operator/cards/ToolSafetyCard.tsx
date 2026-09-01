import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AgentTraceTrustDenialSummary } from "@/core/agent-trace/api";
import { cn } from "@/lib/utils";
import { GateStat } from "../../replay-panel";
import { ListChecksIcon, ShieldAlertIcon } from "lucide-react";
import { useOperatorCopy } from "../use-operator-copy";

export function ToolSafetyCard({
  summary,
  busy,
  onQueuePolicyReview,
}: {
  summary: AgentTraceTrustDenialSummary;
  busy: boolean;
  onQueuePolicyReview: () => void;
}) {
  const to = useOperatorCopy();
  const recent = summary.recent ?? [];
  const topTool = Object.entries(summary.by_tool ?? {}).sort(
    (lhs, rhs) => rhs[1] - lhs[1],
  )[0];
  const canQueue = summary.total >= 2;
  return (
    <div
      className={cn(
        "mt-3 rounded-lg border px-3 py-2",
        summary.total > 0
          ? "border-warning/30 bg-warning/10"
          : "border-border-default bg-muted/15",
      )}
    >
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            <ShieldAlertIcon
              className={cn(
                "size-4",
                summary.total > 0
                  ? "text-warning"
                  : "text-muted-foreground",
              )}
            />
            {to("Tool safety")}
            <Badge variant="outline" className="text-xs">
              {summary.total} {to("denied")}
            </Badge>
          </div>
          <div className="mt-1 truncate text-xs text-muted-foreground">
            {summary.total > 0
              ? `${topTool?.[0] ?? "tool"} has recent policy denials`
              : to("No static tool denials recorded in current trace window")}
          </div>
        </div>
        <div className="flex shrink-0 items-start gap-3">
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-right font-mono text-xs">
            <GateStat label={to("deny")} value={summary.by_action.deny ?? 0} />
            <GateStat
              label={to("block")}
              value={summary.by_action.block ?? 0}
            />
            <GateStat label={to("halt")} value={summary.by_action.halt ?? 0} />
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8 shrink-0 px-2 text-xs"
            disabled={!canQueue || busy}
            onClick={onQueuePolicyReview}
          >
            <ListChecksIcon className="mr-1.5 size-3.5" />
            {to("Queue policy review")}
          </Button>
        </div>
      </div>
      {recent.length > 0 && (
        <div className="mt-2 space-y-1.5">
          {recent.slice(-2).map((item, index) => (
            <div
              key={`${item.id ?? index}:${item.tool_name}`}
              className="rounded-md border border-background/70 bg-background/60 px-2 py-1.5"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0 truncate text-xs font-medium">
                  {item.tool_name}
                </div>
                <Badge variant="outline" className="shrink-0 text-xs">
                  {item.risk_level || item.action}
                </Badge>
              </div>
              <div className="mt-1 truncate text-xs text-muted-foreground">
                {item.reason || item.action}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
