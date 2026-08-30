import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { SubagentFitnessReport } from "@/core/agent-trace/api";
import { cn } from "@/lib/utils";
import { GateStat } from "../../replay-panel";
import { ShieldAlertIcon } from "lucide-react";
import { useOperatorCopy } from "../use-operator-copy";

export function SubagentRiskCard({
  report,
  busyId,
  onWatch,
  onRetire,
}: {
  report: SubagentFitnessReport;
  busyId: string | null;
  onWatch: (role: string, evidenceItemIds: string[]) => void;
  onRetire: (role: string, evidenceItemIds: string[]) => void;
}) {
  const to = useOperatorCopy();
  const risks = report.top_risks.slice(0, 3);
  const hasRisks = risks.length > 0;
  return (
    <div
      className={cn(
        "mt-3 rounded-lg border px-3 py-2",
        hasRisks
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
                hasRisks
                  ? "text-warning"
                  : "text-muted-foreground",
              )}
            />
            {to("Subagent risk")}
            <Badge variant="outline" className="text-xs">
              {report.role_count} {to("roles")}
            </Badge>
          </div>
          <div className="mt-1 truncate text-xs text-muted-foreground">
            {hasRisks
              ? to(
                  "Route evidence has identified watch or retirement candidates",
                )
              : to(
                  "No watch or retirement candidates in current fitness evidence",
                )}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2 text-right font-mono text-xs">
          <GateStat label={to("risks")} value={report.top_risks.length} />
          <GateStat
            label={to("route")}
            value={report.top_risks.reduce(
              (total, item) => total + (item.routing_evidence_count ?? 0),
              0,
            )}
          />
        </div>
      </div>
      {hasRisks && (
        <div className="mt-2 space-y-1.5">
          {risks.map((item) => (
            <div
              key={item.role}
              className="rounded-md border border-background/70 bg-background/60 px-2 py-1.5"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0 truncate text-xs font-medium">
                  {item.role}
                </div>
                <Badge
                  variant="outline"
                  className={cn(
                    "shrink-0 text-xs",
                    item.verdict === "retire_candidate"
                      ? "border-destructive/30 bg-destructive/10 text-destructive"
                      : "border-warning/30 bg-warning/10 text-warning",
                  )}
                >
                  {item.verdict}
                </Badge>
              </div>
              <div className="mt-1 flex flex-wrap gap-2 text-xs text-muted-foreground">
                <span>
                  {to("score")} {item.score.toFixed(2)}
                </span>
                <span>
                  {item.sample_count} {to("samples")}
                </span>
                {(item.routing_evidence_count ?? 0) > 0 && (
                  <span>
                    {item.routing_evidence_count} {to("route evidence")}
                  </span>
                )}
                {item.by_evidence_source?.deep_research_route_decision ? (
                  <span>
                    {item.by_evidence_source.deep_research_route_decision}{" "}
                    {to("deep research")}
                  </span>
                ) : null}
              </div>
              <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                {item.recommendation}
              </div>
              <div className="mt-2 flex justify-end gap-1.5">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  disabled={busyId === `subagent-policy:${item.role}:watch`}
                  onClick={() => onWatch(item.role, item.evidence_item_ids)}
                >
                  {to("Watch")}
                </Button>
                <Button
                  type="button"
                  variant="destructive"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  disabled={busyId === `subagent-policy:${item.role}:retire`}
                  onClick={() => onRetire(item.role, item.evidence_item_ids)}
                >
                  {to("Retire")}
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
