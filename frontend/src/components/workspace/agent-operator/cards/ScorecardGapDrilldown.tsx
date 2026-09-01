import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AgentCompetitorScorecard, AgentTracePromotionAuditSummary, AgentTraceReviewQueueItem } from "@/core/agent-trace/api";
import { cn } from "@/lib/utils";
import { CheckCircle2Icon, ListChecksIcon } from "lucide-react";
import { competitorLabel } from "../operator-utils";
import { useOperatorCopy } from "../use-operator-copy";

export function ScorecardGapDrilldown({
  gap,
  checklist,
  queueItem,
  auditSummary,
  queueBusy,
  applyBusy,
  onQueue,
  onApplyPromoted,
}: {
  gap: AgentCompetitorScorecard["dimensions"][number];
  checklist: NonNullable<
    AgentCompetitorScorecard["dimensions"][number]["echo_evidence_checklist"]
  >;
  queueItem: AgentTraceReviewQueueItem | null;
  auditSummary: AgentTracePromotionAuditSummary;
  queueBusy: boolean;
  applyBusy: boolean;
  onQueue: () => void;
  onApplyPromoted: () => void;
}) {
  const to = useOperatorCopy();
  const realScore = gap.echo_baseline_score ?? gap.scores.echo;
  const evidenceScore =
    gap.echo_evidence_adjusted_score ??
    gap.evidence_adjusted_scores?.echo ??
    realScore;
  const nextActions = gap.echo_next_actions ?? [];
  const operatorDrilldown = gap.operator_drilldown;
  const drilldownLinks = operatorDrilldown?.links ?? [];
  return (
    <div
      id="scorecard-gap-drilldown"
      role="region"
      aria-label={`${to("Scorecard gap drill-down for")} ${gap.title}`}
      className="mt-2 rounded-md border border-background/70 bg-background/60 px-2 py-1.5"
    >
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="text-xs font-semibold">{gap.title}</div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            {gap.why}
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap gap-1.5">
          {queueItem ? (
            <Badge
              variant="outline"
              className="border-info/25 bg-info/10 text-xs text-info"
            >
              {to("queued")} {queueItem.priority}
            </Badge>
          ) : null}
          <Badge variant="outline" className="text-xs">
            {to("real")} {realScore}
          </Badge>
          <Badge variant="outline" className="text-xs">
            {to("evidence")} {evidenceScore}
          </Badge>
          <Badge variant="outline" className="text-xs">
            {to("effective gap")}{" "}
            {gap.echo_gap_to_effective_target ?? gap.echo_gap_to_target}
          </Badge>
          <Badge variant="outline" className="text-xs">
            {to("surpass gap")} {gap.echo_gap_to_surpass ?? 0}
          </Badge>
          {gap.best_external_competitor && (
            <Badge variant="outline" className="text-xs">
              {to("best")} {competitorLabel(gap.best_external_competitor)}{" "}
              {gap.best_external_score ?? 0}
            </Badge>
          )}
        </div>
      </div>

      <div className="mt-2 flex flex-col gap-2 rounded-md border border-border-default bg-muted/15 px-2 py-1.5 md:flex-row md:items-center md:justify-between">
        <div className="min-w-0">
          <div className="text-xs font-medium text-muted-foreground">
            {to("Remediation queue")}
          </div>
          <div className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
            {queueItem
              ? `${queueItem.id} · ${queueItem.status} · x${queueItem.occurrences}`
              : to("not queued")}
          </div>
          {queueItem ? (
            <div className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
              {to("target")} {queueItem.target_bucket} · {to("audit")}{" "}
              {auditSummary.total}
            </div>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-wrap gap-1.5">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs"
            disabled={queueBusy}
            onClick={onQueue}
          >
            <ListChecksIcon
              className={cn("mr-1.5 size-3", queueBusy && "animate-spin")}
            />
            {queueItem ? to("Refresh queue item") : to("Queue this gap")}
          </Button>
          {queueItem ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 px-2 text-xs"
              disabled={applyBusy || queueItem.status !== "promoted"}
              onClick={onApplyPromoted}
            >
              <CheckCircle2Icon
                className={cn("mr-1.5 size-3", applyBusy && "animate-spin")}
              />
              {to("Apply gap")}
            </Button>
          ) : null}
        </div>
      </div>

      {nextActions.length > 0 && (
        <div className="mt-2 grid gap-1.5 lg:grid-cols-2">
          {nextActions.slice(0, 2).map((action) => (
            <div
              key={action}
              className="rounded-md border border-warning/20 bg-warning/10 px-2 py-1.5 text-xs text-warning"
            >
              {action}
            </div>
          ))}
        </div>
      )}

      {operatorDrilldown?.schema ===
        "echo.scorecard_operator_drilldown.v1" &&
        drilldownLinks.length > 0 && (
          <div className="mt-2 rounded-md border border-border-default bg-muted/15 px-2 py-1.5">
            <div className="mb-1 flex items-center justify-between gap-2">
              <div className="min-w-0 truncate text-xs font-medium text-muted-foreground">
                {to("Evidence sources")}
              </div>
              <Badge variant="outline" className="shrink-0 text-xs">
                {drilldownLinks.length} {to("links")}
              </Badge>
            </div>
            <div className="grid gap-1.5 lg:grid-cols-2">
              {drilldownLinks.slice(0, 4).map((link) => (
                <div
                  key={`${link.id ?? link.label}-${link.href}`}
                  className="min-w-0 rounded-md border border-border-default bg-background/50 px-2 py-1.5"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0 truncate text-xs font-medium">
                      {link.label ?? link.id ?? to("Evidence link")}
                    </div>
                    {link.method ? (
                      <Badge variant="outline" className="shrink-0 text-xs">
                        {link.method}
                      </Badge>
                    ) : null}
                  </div>
                  {link.href ? (
                    <div className="mt-1 truncate font-mono text-xs text-muted-foreground">
                      {link.href}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        )}

      {checklist.length > 0 && (
        <div className="mt-2">
          <div className="mb-1 flex items-center justify-between gap-2">
            <div className="min-w-0 truncate text-xs font-medium text-muted-foreground">
              {to("Evidence checklist")}
            </div>
            <Badge variant="outline" className="shrink-0 text-xs">
              {gap.echo_missing_evidence_count ?? 0} {to("missing")}
            </Badge>
          </div>
          <div className="grid gap-1.5 lg:grid-cols-2">
            {checklist.slice(0, 2).map((item) => (
              <div
                key={item.id ?? item.title}
                className="rounded-md border border-border-default bg-muted/15 px-2 py-1.5"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0 truncate text-xs font-medium">
                    {item.title ?? item.id ?? "evidence"}
                  </div>
                  <Badge variant="outline" className="shrink-0 text-xs">
                    {Math.round((item.score ?? 0) * 100)}%
                  </Badge>
                </div>
                <div className="mt-1 flex flex-wrap gap-1.5 text-xs text-muted-foreground">
                  <span>
                    {to("impl")} {item.implementation.present}/
                    {item.implementation.total}
                  </span>
                  <span>
                    {to("tests")} {item.tests.present}/{item.tests.total}
                  </span>
                  {item.implementation.missing_count +
                    item.tests.missing_count >
                    0 && (
                    <span className="text-warning">
                      {item.implementation.missing_count +
                        item.tests.missing_count}{" "}
                      {to("missing")}
                    </span>
                  )}
                </div>
                {item.next_actions[0] && (
                  <div className="mt-1 truncate text-xs text-muted-foreground">
                    {item.next_actions[0]}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
