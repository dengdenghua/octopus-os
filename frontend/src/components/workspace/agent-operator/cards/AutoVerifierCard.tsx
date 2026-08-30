import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AutoVerifierMetricsReport, RepairRouteQualityReport } from "@/core/agent-trace/api";
import { cn } from "@/lib/utils";
import { GateStat } from "../../replay-panel";
import { GitBranchIcon, ListChecksIcon } from "lucide-react";
import { useOperatorCopy } from "../use-operator-copy";

export function AutoVerifierCard({
  report,
  repairRoutes,
  queueBusy,
  onQueueRepairRoutes,
}: {
  report: AutoVerifierMetricsReport;
  repairRoutes: RepairRouteQualityReport;
  queueBusy: boolean;
  onQueueRepairRoutes: () => void;
}) {
  const to = useOperatorCopy();
  const decisions = report.recent_decisions ?? [];
  const latest = decisions.length > 0 ? decisions[decisions.length - 1] : null;
  const candidates = latest?.candidates?.slice(0, 2) ?? [];
  const alerts = report.alerts ?? [];
  const repairCandidates = repairRoutes.promotion_candidates ?? [];
  const passPercent = Math.round((report.pass_rate ?? 0) * 100);
  const repairScorePercent = Math.round((repairRoutes.score ?? 0) * 100);
  const repairBlockers = repairRoutes.quality_gate?.blockers ?? [];
  const hasSignal = report.total > 0 || latest !== null;
  return (
    <div
      className={cn(
        "mt-3 rounded-lg border px-3 py-2",
        alerts.length > 0
          ? "border-destructive/30 bg-destructive/10"
          : report.fail_count > 0
            ? "border-warning/30 bg-warning/10"
            : hasSignal
              ? "border-success/25 bg-success/10"
              : "border-border-default bg-muted/15",
      )}
    >
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            <ListChecksIcon className="size-4 text-primary" />
            {to("Auto verifier")}
            <Badge variant="outline" className="text-xs">
              {passPercent}% {to("pass")}
            </Badge>
            <Badge
              variant={repairRoutes.ready ? "outline" : "destructive"}
              className="text-xs"
            >
              {to("routes")} {repairScorePercent}%
            </Badge>
          </div>
          <div className="mt-1 truncate text-xs text-muted-foreground">
            {alerts[0]?.message ??
              (latest
                ? latest.selected_command
                : to("No auto-verifier decisions recorded yet"))}
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-right font-mono text-xs">
          <GateStat label={to("runs")} value={report.total} />
          <GateStat label={to("pass")} value={report.pass_count} />
          <GateStat label={to("fail")} value={report.fail_count} />
          <GateStat
            label="ms"
            value={Math.round(report.avg_duration_ms ?? 0)}
          />
        </div>
      </div>
      <div className="mt-2 flex flex-col gap-2 rounded-md border border-background/70 bg-background/60 px-2 py-1.5 md:flex-row md:items-center md:justify-between">
        <div className="min-w-0 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">
            {repairCandidates.length}
          </span>{" "}
          {to("repair-route promotion candidate(s)")}
          {repairCandidates[0]?.route
            ? ` · top ${repairCandidates[0].route}`
            : ""}
          {repairBlockers.length > 0
            ? ` · blocked by ${repairBlockers[0]?.replaceAll("_", " ") ?? ""}`
            : ""}
        </div>
        <Button
          variant="outline"
          size="sm"
          className="h-7 shrink-0 px-2 text-xs"
          onClick={onQueueRepairRoutes}
          disabled={queueBusy || repairCandidates.length === 0}
        >
          <GitBranchIcon className="mr-1.5 size-3" />
          {to("Queue routes")}
        </Button>
      </div>
      {alerts.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {alerts.slice(0, 3).map((alert) => (
            <Badge
              key={`${alert.family}:${alert.severity}`}
              variant="outline"
              className="border-destructive/30 bg-destructive/10 text-xs text-destructive"
            >
              {alert.family} {to("drift")}{" "}
              {Math.round(alert.pass_rate * 100)}%
            </Badge>
          ))}
        </div>
      )}
      {candidates.length > 0 && (
        <div className="mt-2 space-y-1.5">
          {candidates.map((candidate) => (
            <div
              key={`${candidate.rank}:${candidate.command}`}
              className="rounded-md border border-background/70 bg-background/60 px-2 py-1.5"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0 truncate font-mono text-xs">
                  #{candidate.rank} {candidate.command}
                </div>
                <Badge variant="outline" className="shrink-0 text-xs">
                  {candidate.family}
                </Badge>
              </div>
              <div className="mt-1 flex flex-wrap gap-2 text-xs text-muted-foreground">
                <span>
                  {Math.round(candidate.pass_rate * 100)}% {to("history")}
                </span>
                <span>
                  {candidate.history_count} {to("samples")}
                </span>
                <span>{Math.round(candidate.avg_duration_ms)}ms</span>
              </div>
              <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                {candidate.reason}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
