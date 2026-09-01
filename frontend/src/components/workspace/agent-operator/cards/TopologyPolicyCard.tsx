import { Badge } from "@/components/ui/badge";
import type { OrganizationTopology } from "@/core/agent-trace/api";
import { cn } from "@/lib/utils";
import { GateStat } from "../../replay-panel";
import { GitBranchIcon } from "lucide-react";
import { useOperatorCopy } from "../use-operator-copy";

export function TopologyPolicyCard({
  topologies,
}: {
  topologies: OrganizationTopology[];
}) {
  const to = useOperatorCopy();
  const impacted = topologies.filter((topology) => {
    const policy = topology.subagent_policy;
    return policy?.blocked || (policy?.watch_count ?? 0) > 0;
  });
  const blocked = impacted.filter(
    (topology) => topology.subagent_policy?.blocked,
  );
  const watchCount = impacted.reduce(
    (total, topology) => total + (topology.subagent_policy?.watch_count ?? 0),
    0,
  );
  return (
    <div
      className={cn(
        "mt-3 rounded-lg border px-3 py-2",
        blocked.length > 0
          ? "border-destructive/30 bg-destructive/10"
          : impacted.length > 0
            ? "border-warning/30 bg-warning/10"
            : "border-border-default bg-muted/15",
      )}
    >
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            <GitBranchIcon className="size-4 text-primary" />
            {to("Topology policy")}
            <Badge variant="outline" className="text-xs">
              {topologies.length} {to("teams")}
            </Badge>
          </div>
          <div className="mt-1 truncate text-xs text-muted-foreground">
            {blocked.length > 0
              ? to(
                  "Operator-retired subagents are present in active topologies",
                )
              : impacted.length > 0
                ? to("Watched subagents are present in active topologies")
                : to("No active topology is affected by subagent policy")}
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-right font-mono text-xs">
          <GateStat label={to("blocked")} value={blocked.length} />
          <GateStat label={to("watch")} value={watchCount} />
          <GateStat label={to("teams")} value={topologies.length} />
        </div>
      </div>
      {impacted.length > 0 && (
        <div className="mt-2 space-y-1.5">
          {impacted.slice(0, 3).map((topology) => (
            <div
              key={topology.fingerprint}
              className="rounded-md border border-background/70 bg-background/60 px-2 py-1.5"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0 truncate text-xs font-medium">
                  {topology.name}
                </div>
                <Badge
                  variant="outline"
                  className={cn(
                    "shrink-0 text-xs",
                    topology.subagent_policy?.blocked
                      ? "border-destructive/30 bg-destructive/10 text-destructive"
                      : "border-warning/30 bg-warning/10 text-warning",
                  )}
                >
                  {topology.subagent_policy?.status ?? to("clear")}
                </Badge>
              </div>
              <div className="mt-1 flex flex-wrap gap-2 text-xs text-muted-foreground">
                {[
                  ...(topology.subagent_policy?.retired ?? []),
                  ...(topology.subagent_policy?.watch ?? []),
                ]
                  .slice(0, 3)
                  .map((item) => (
                    <span
                      key={`${topology.fingerprint}:${item.role}:${item.agent_id}`}
                    >
                      {item.role}:{item.agent_id}
                    </span>
                  ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
