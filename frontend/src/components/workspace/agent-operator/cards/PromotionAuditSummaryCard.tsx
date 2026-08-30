import type { AgentTracePromotionAuditSummary } from "@/core/agent-trace/api";
import { cn } from "@/lib/utils";
import { GateStat } from "../../replay-panel";
import { formatOperatorCopy } from "../operator-utils";
import { useOperatorCopy } from "../use-operator-copy";

export function PromotionAuditSummaryCard({
  summary,
}: {
  summary: AgentTracePromotionAuditSummary;
}) {
  const to = useOperatorCopy();
  const topologyBlocks = summary.topology_policy_block_count ?? 0;
  const integrity = summary.integrity;
  const integrityOk = integrity?.ok ?? true;
  const risky =
    summary.gate_blocked_override_count > 0 ||
    topologyBlocks > 0 ||
    !integrityOk;
  return (
    <div
      className={cn(
        "mt-3 rounded-lg border px-3 py-2",
        risky
          ? "border-warning/30 bg-warning/10"
          : "border-border-default bg-muted/15",
      )}
    >
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div className="min-w-0">
          <div className="text-sm font-medium">{to("Promotion audit")}</div>
          <div className="mt-1 truncate text-xs text-muted-foreground">
            {!integrityOk
              ? formatOperatorCopy(to, "Audit chain broken at #{at}", {
                  at: integrity?.broken_at ?? "?",
                })
              : topologyBlocks > 0
                ? to("Operator policy blocked team topology attempts")
                : risky
                  ? to("Overrides were used after replay gate blocked apply")
                  : to("No blocked gate overrides recorded")}
          </div>
          <div className="mt-1 truncate text-xs text-muted-foreground">
            {to("chain")} {integrityOk ? to("ok") : to("failed")} ·{" "}
            {integrity?.entries_checked ?? 0} {to("checked")}
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-right font-mono text-xs">
          <GateStat label={to("audit")} value={summary.total} />
          <GateStat label={to("over")} value={summary.override_count} />
          <GateStat label={to("gate")} value={summary.gate_failed_count} />
          <GateStat label={to("topo")} value={topologyBlocks} />
        </div>
      </div>
    </div>
  );
}
