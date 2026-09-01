import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AgentCompetitorScorecard, AgentTracePromotionAuditSummary, AgentTraceReviewQueueItem } from "@/core/agent-trace/api";
import { cn } from "@/lib/utils";
import { GateStat } from "../../replay-panel";
import { BarChart3Icon, ListChecksIcon } from "lucide-react";
import { ScorecardGapDrilldown } from "./ScorecardGapDrilldown";
import { competitorLabel, formatOperatorCopy, scorecardGapQueueItemForDimension } from "../operator-utils";
import { useOperatorCopy } from "../use-operator-copy";

export function CompetitorScorecardCard({
  report,
  error,
  auditSummary,
  queueItems,
  queueBusy,
  busyId,
  applyBusy,
  onQueueRealGaps,
  onQueueGap,
  onApplyPromoted,
}: {
  report: AgentCompetitorScorecard;
  error?: string | null;
  auditSummary: AgentTracePromotionAuditSummary;
  queueItems: AgentTraceReviewQueueItem[];
  queueBusy: boolean;
  busyId: string | null;
  applyBusy: boolean;
  onQueueRealGaps: () => void;
  onQueueGap: (dimensionId: string) => void;
  onApplyPromoted: () => void;
}) {
  const to = useOperatorCopy();
  const [selectedGapId, setSelectedGapId] = useState<string | null>(null);
  const echoScore = report.overall.echo ?? 0;
  const evidenceAdjustedEchoScore =
    report.evidence_adjusted_overall?.echo ?? echoScore;
  const belowTarget = report.echo_below_target ?? [];
  const focusGaps = report.echo_focus_gaps ?? belowTarget;
  const externalGaps =
    report.echo_external_gap_dimensions ??
    report.echo_external_leaders ??
    [];
  const strengths = report.echo_strengths ?? [];
  const certification = report.parity_certification;
  const evidenceLayers = report.evidence_layers;
  const behavioralEvidence = evidenceLayers?.behavioral_head_to_head;
  const topGap = focusGaps
    .slice()
    .sort(
      (lhs, rhs) =>
        (rhs.echo_gap_to_effective_target ?? rhs.echo_gap_to_target) -
        (lhs.echo_gap_to_effective_target ?? lhs.echo_gap_to_target),
    )[0];
  const selectedGapCandidate =
    focusGaps.find((dimension) => dimension.id === selectedGapId) ?? topGap;
  const selectedGap = selectedGapCandidate
    ? (belowTarget.find(
        (dimension) => dimension.id === selectedGapCandidate.id,
      ) ?? selectedGapCandidate)
    : undefined;
  const selectedGapChecklist = selectedGap?.echo_evidence_checklist ?? [];
  const selectedGapQueueItem = selectedGap
    ? scorecardGapQueueItemForDimension(queueItems, selectedGap.id)
    : null;
  const healthy =
    echoScore >= report.target_score &&
    externalGaps.length === 0 &&
    (!behavioralEvidence || behavioralEvidence.ready);
  return (
    <div
      className={cn(
        "mt-3 rounded-lg border px-3 py-2",
        error
          ? "border-warning/30 bg-warning/10"
          : healthy
            ? "border-success/25 bg-success/10"
            : "border-warning/30 bg-warning/10",
      )}
    >
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            <BarChart3Icon
              className={cn(
                "size-4",
                !error && healthy
                  ? "text-success"
                  : "text-warning",
              )}
            />
            {to("Competitor scorecard")}
            <Badge variant="outline" className="text-xs">
              {error ? to("degraded") : report.verdict.replaceAll("_", " ")}
            </Badge>
          </div>
          <div className="mt-1 truncate text-xs text-muted-foreground">
            {error
              ? error
              : topGap
                ? `${topGap.title} ${to("gap")} ${
                    topGap.echo_gap_to_effective_target ??
                    topGap.echo_gap_to_target
                  } ${to("vs effective target")}`
                : behavioralEvidence && !behavioralEvidence.ready
                  ? to("Behavioral head-to-head is not certified")
                  : certification?.ready
                    ? formatOperatorCopy(to, "Certification passed {passed}/{total}", {
                        passed: certification.passed,
                        total: certification.total,
                      })
                    : to("Echo has no tracked effective scorecard gaps")}
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            {to(
              "Architecture is estimated; static certification and same-task behavioral evidence are tracked separately.",
            )}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-right font-mono text-xs xl:grid-cols-6">
            <GateStat label={to("Architecture")} value={echoScore} />
            <GateStat
              label={to("Static evidence")}
              value={evidenceAdjustedEchoScore}
            />
            {behavioralEvidence && (
              <GateStat
                label={to("Behavior %")}
                value={Math.round(behavioralEvidence.echo_pass_pow_k * 100)}
              />
            )}
            <GateStat label="Codex" value={report.overall.codex ?? 0} />
            <GateStat label="Claude" value={report.overall.claude_code ?? 0} />
            <GateStat label="OpenClaw" value={report.overall.openclaw ?? 0} />
            <GateStat label="Hermes" value={report.overall.hermes ?? 0} />
          </div>
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs"
            disabled={queueBusy || focusGaps.length === 0}
            onClick={onQueueRealGaps}
          >
            <ListChecksIcon
              className={cn("mr-1.5 size-3", queueBusy && "animate-spin")}
            />
            {to("Queue real gaps")}
          </Button>
        </div>
      </div>

      <div className="mt-2 grid gap-2 lg:grid-cols-[0.95fr_1.05fr]">
        <div className="rounded-md border border-background/70 bg-background/60 px-2 py-1.5">
          <div className="mb-1 text-xs font-medium text-muted-foreground">
            {to("Real comparison ranking")}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {(report.ranking ?? []).map((row, index) => (
              <Badge
                key={row.competitor}
                variant="outline"
                className={cn(
                  "text-xs",
                  row.competitor === "echo" &&
                    "border-primary/30 bg-primary/10 text-primary",
                )}
              >
                #{index + 1} {competitorLabel(row.competitor)} {row.score}
              </Badge>
            ))}
          </div>
        </div>
        <div className="rounded-md border border-background/70 bg-background/60 px-2 py-1.5">
          <div className="mb-1 text-xs font-medium text-muted-foreground">
            {to("Effective focus gaps")}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {focusGaps.length === 0 ? (
              <>
                <Badge variant="outline" className="text-xs">
                  {to("clear")}
                </Badge>
                {certification && (
                  <Badge
                    variant="outline"
                    className={cn(
                      "text-xs",
                      certification.ready
                        ? "border-success/25 bg-success/10 text-success"
                        : "border-warning/30 bg-warning/10 text-warning",
                    )}
                  >
                    {to("certified")} {certification.passed}/
                    {certification.total}
                  </Badge>
                )}
              </>
            ) : (
              focusGaps.slice(0, 5).map((dimension) => (
                <button
                  key={dimension.id}
                  type="button"
                  aria-controls="scorecard-gap-drilldown"
                  aria-pressed={selectedGap?.id === dimension.id}
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-xs text-warning transition-colors hover:bg-warning/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-warning/60",
                    selectedGap?.id === dimension.id
                      ? "border-warning/60 bg-warning/20"
                      : "border-warning/30 bg-warning/10",
                  )}
                  onClick={() => setSelectedGapId(dimension.id)}
                >
                  {dimension.title}{" "}
                  {dimension.echo_gap_to_effective_target ??
                    dimension.echo_gap_to_target}
                </button>
              ))
            )}
          </div>
          {externalGaps.length > 0 && (
            <div className="mt-1 truncate text-xs text-muted-foreground">
              {to("external leader gaps")}: {externalGaps.length}
            </div>
          )}
        </div>
      </div>

      {selectedGap && (
        <ScorecardGapDrilldown
          gap={selectedGap}
          checklist={selectedGapChecklist}
          queueItem={selectedGapQueueItem}
          auditSummary={auditSummary}
          queueBusy={busyId === `queue-scorecard-gap:${selectedGap.id}`}
          applyBusy={applyBusy}
          onQueue={() => onQueueGap(selectedGap.id)}
          onApplyPromoted={onApplyPromoted}
        />
      )}

      <div className="mt-2 flex flex-wrap gap-1.5">
        {strengths.slice(0, 3).map((dimension) => (
          <Badge
            key={dimension.id}
            variant="outline"
            className="border-success/25 bg-success/10 text-xs text-success"
          >
            {to("leads")} {dimension.title} {dimension.scores.echo}
          </Badge>
        ))}
        {report.next_focus.slice(0, 2).map((item) => (
          <Badge
            key={item}
            variant="outline"
            className="max-w-full text-xs"
          >
            <span className="truncate">{item}</span>
          </Badge>
        ))}
      </div>
    </div>
  );
}
