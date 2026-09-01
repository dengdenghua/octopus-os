import { Badge } from "@/components/ui/badge";
import type { E2ESurpassCertification } from "@/core/agent-trace/api";
import { cn } from "@/lib/utils";
import { GateStat } from "../../replay-panel";
import { CheckCircle2Icon, XCircleIcon } from "lucide-react";
import { useOperatorCopy } from "../use-operator-copy";

export function E2ESurpassCertificationCard({
  certification,
  error,
}: {
  certification: E2ESurpassCertification;
  error?: string | null;
}) {
  const to = useOperatorCopy();
  const summary = certification.summary;
  const failedChecks = certification.checks.filter((check) => !check.passed);
  const passedChecks = certification.checks.length - failedChecks.length;
  const ready = !error && certification.ready;
  const behavioralReady = summary.behavioral_ready;
  const behavioralBlocked = Boolean(
    certification.behavioral?.infrastructure?.active,
  );
  const focusText = error
    ? error
    : ready
      ? to(
          "same-task repeated behavioral runs and static release gates clear the Codex bar",
        )
      : failedChecks[0]?.title ||
        certification.next_actions[0] ||
        to("waiting for E2E certification evidence");
  return (
    <div
      className={cn(
        "mt-3 rounded-lg border px-3 py-2",
        ready
          ? "border-success/25 bg-success/10"
          : "border-warning/30 bg-warning/10",
      )}
    >
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            {ready ? (
              <CheckCircle2Icon className="size-4 text-success" />
            ) : (
              <XCircleIcon className="size-4 text-warning" />
            )}
            {to("E2E surpass certification")}
            <Badge
              variant="outline"
              className={cn(
                "text-xs",
                ready
                  ? "border-success/25 bg-success/10 text-success"
                  : "border-warning/30 bg-warning/10 text-warning",
              )}
            >
              {error
                ? to("degraded")
                : certification.verdict.replaceAll("_", " ")}
            </Badge>
            <Badge variant="outline" className="text-xs">
              {to("quality")} {summary.quality_ready}/{summary.quality_total}
            </Badge>
            <Badge
              variant="outline"
              className={cn(
                "text-xs",
                behavioralReady
                  ? "border-success/25 bg-success/10 text-success"
                  : "border-warning/30 bg-warning/10 text-warning",
              )}
            >
              {to("behavior")}{" "}
              {behavioralReady
                ? to("verified")
                : behavioralBlocked
                  ? to("provider blocked")
                  : to("missing")}
            </Badge>
          </div>
          <div className="mt-1 truncate text-xs text-muted-foreground">
            {focusText}
          </div>
          {!error && (
            <div className="mt-1 truncate text-xs text-muted-foreground">
              {to("scorecard")} {summary.scorecard_echo}{" "}
              {to("vs best external")} {summary.scorecard_best_external} ·{" "}
              {to("automation")} {summary.automation_echo} {to("vs Codex")}{" "}
              {summary.automation_codex}
              {behavioralReady && (
                <>
                  {" "}
                  · {to("pass^k")}{" "}
                  {Math.round(summary.behavioral_echo_pass_pow_k * 100)}%{" "}
                  {to("vs Codex")}{" "}
                  {Math.round(summary.behavioral_codex_pass_pow_k * 100)}%
                </>
              )}
            </div>
          )}
        </div>
        <div className="grid shrink-0 grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2 text-right font-mono text-xs">
          <GateStat label={to("Scorecard")} value={summary.scorecard_echo} />
          <GateStat
            label={to("Evidence")}
            value={summary.scorecard_evidence_adjusted_echo}
          />
          <GateStat
            label={to("Automation")}
            value={summary.automation_echo}
          />
          <GateStat label={to("Quality")} value={summary.quality_ready} />
          <GateStat
            label={
              behavioralBlocked ? to("Behavior blocked") : to("Behavior")
            }
            value={
              behavioralReady
                ? Math.round(summary.behavioral_echo_pass_pow_k * 100)
                : 0
            }
          />
        </div>
      </div>

      <div className="mt-2 grid gap-2 lg:grid-cols-[0.9fr_1.1fr]">
        <div className="rounded-md border border-background/70 bg-background/60 px-2 py-1.5">
          <div className="mb-1 flex items-center justify-between gap-2">
            <div className="min-w-0 truncate text-xs font-medium text-muted-foreground">
              {to("Certification checks")}
            </div>
            <Badge variant="outline" className="shrink-0 text-xs">
              {passedChecks}/{certification.checks.length}
            </Badge>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {failedChecks.length === 0 && certification.checks.length > 0 ? (
              <Badge
                variant="outline"
                className="border-success/25 bg-success/10 text-xs text-success"
              >
                {to("all checks passed")}
              </Badge>
            ) : (
              failedChecks.slice(0, 3).map((check) => (
                <Badge
                  key={check.id}
                  variant="outline"
                  className="border-warning/30 bg-warning/10 text-xs text-warning"
                >
                  {check.title} {check.score}/{check.target}
                </Badge>
              ))
            )}
          </div>
        </div>
        <div className="rounded-md border border-background/70 bg-background/60 px-2 py-1.5">
          <div className="mb-1 text-xs font-medium text-muted-foreground">
            {to("Gap counters")}
          </div>
          <div className="flex flex-wrap gap-1.5">
            <Badge variant="outline" className="text-xs">
              {to("scorecard gaps")} {summary.scorecard_gap_dimensions}
            </Badge>
            <Badge variant="outline" className="text-xs">
              {to("automation gaps")} {summary.automation_gap_dimensions}
            </Badge>
            <Badge
              variant="outline"
              className={cn(
                "text-xs",
                summary.all_dimensions_surpassed &&
                  "border-success/25 bg-success/10 text-success",
              )}
            >
              {to("dimensions")}{" "}
              {summary.all_dimensions_surpassed
                ? to("surpassed")
                : to("open")}
            </Badge>
          </div>
        </div>
      </div>
    </div>
  );
}
