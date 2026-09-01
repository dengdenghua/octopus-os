import { Badge } from "@/components/ui/badge";
import type { OrganizationTopologyLiftReport, OrganizationTopologyProposalsReport } from "@/core/agent-trace/api";
import { cn } from "@/lib/utils";
import { GateStat } from "../../replay-panel";
import { GitBranchIcon } from "lucide-react";
import { useOperatorCopy } from "../use-operator-copy";

export function TopologyPromotionCard({
  proposals,
  lift,
}: {
  proposals: OrganizationTopologyProposalsReport;
  lift: OrganizationTopologyLiftReport;
}) {
  const to = useOperatorCopy();
  const subagentProposals = proposals.subagent_promotion_count ?? 0;
  const improved = lift.reports.filter(
    (item) => item.verdict === "improved",
  ).length;
  const regressed = lift.reports.filter(
    (item) => item.verdict === "regressed",
  ).length;
  const pending = lift.reports.filter(
    (item) => item.verdict === "pending_after_runs",
  ).length;
  const hasSignal = subagentProposals > 0 || lift.count > 0;
  return (
    <div
      className={cn(
        "mt-3 rounded-lg border px-3 py-2",
        regressed > 0
          ? "border-destructive/30 bg-destructive/10"
          : hasSignal
            ? "border-success/25 bg-success/10"
            : "border-border-default bg-muted/15",
      )}
    >
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            <GitBranchIcon className="size-4 text-primary" />
            {to("Team promotion")}
            <Badge variant="outline" className="text-xs">
              {proposals.count} {to("proposals")}
            </Badge>
          </div>
          <div className="mt-1 truncate text-xs text-muted-foreground">
            {subagentProposals > 0
              ? to("Strong subagents are ready for team topology promotion")
              : lift.count > 0
                ? to("Promotion lift is being tracked from team performance")
                : to("No subagent-derived team promotions yet")}
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-right font-mono text-xs">
          <GateStat label={to("sub")} value={subagentProposals} />
          <GateStat label={to("up")} value={improved} />
          <GateStat label={to("wait")} value={pending} />
          <GateStat label={to("down")} value={regressed} />
        </div>
      </div>
      {proposals.proposals.length > 0 && (
        <div className="mt-2 space-y-1.5">
          {proposals.proposals.slice(0, 2).map((proposal, index) => (
            <div
              key={`${proposal.base_topology}:${String(proposal.detail.new_agent ?? index)}`}
              className="rounded-md border border-background/70 bg-background/60 px-2 py-1.5"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0 truncate text-xs font-medium">
                  {String(proposal.detail.role ?? proposal.kind)} {"->"}{" "}
                  {String(proposal.detail.new_agent ?? "agent")}
                </div>
                <Badge variant="outline" className="shrink-0 text-xs">
                  {((proposal.rank_score ?? proposal.confidence) * 100).toFixed(
                    0,
                  )}
                  %
                </Badge>
              </div>
              {proposal.detail.historical_lift ? (
                <div className="mt-1 text-xs text-success">
                  lift +{proposal.detail.historical_lift.improved_count}/-
                  {proposal.detail.historical_lift.regressed_count}
                </div>
              ) : null}
              <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                {proposal.rationale}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
