import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AutomationPolicyRuleDraftsReport, AutomationRadarReport } from "@/core/agent-trace/api";
import { cn } from "@/lib/utils";
import { GateStat } from "../../replay-panel";
import { ShieldAlertIcon } from "lucide-react";
import { useOperatorCopy } from "../use-operator-copy";

export function AutomationRadarCard({
  radar,
  drafts,
  busyId,
  onInstallDraft,
}: {
  radar: AutomationRadarReport;
  drafts: AutomationPolicyRuleDraftsReport;
  busyId: string | null;
  onInstallDraft: (draftId: string) => void;
}) {
  const to = useOperatorCopy();
  const echoScore = radar.overall.echo ?? 0;
  const codexScore = radar.overall.codex ?? 0;
  const readyDrafts = radar.policy_rule_drafts.ready;
  const topDraft = drafts.drafts[0] ?? null;
  const topGaps = radar.echo_gaps ?? [];
  return (
    <div className="mt-3 rounded-lg border border-border-default bg-background/60 px-3 py-2">
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            <ShieldAlertIcon className="size-4 text-info" />
            {to("Automation radar")}
            <Badge
              variant="outline"
              className={cn(
                "text-xs",
                radar.verdict === "leading" &&
                  "border-success/25 bg-success/10 text-success",
              )}
            >
              {radar.verdict.replaceAll("_", " ")}
            </Badge>
            <Badge
              variant="outline"
              className={cn(
                "text-xs",
                readyDrafts
                  ? "border-success/25 bg-success/10 text-success"
                  : "border-warning/30 bg-warning/10 text-warning",
              )}
            >
              {to("policy drafts")} {radar.policy_rule_drafts.verified}/
              {radar.policy_rule_drafts.total}
            </Badge>
          </div>
          <div className="mt-1 truncate text-xs text-muted-foreground">
            {to(
              "Browser, desktop, visual replay, and signed automation policy coverage.",
            )}
          </div>
        </div>
        <div className="grid shrink-0 grid-cols-2 sm:grid-cols-3 gap-2 text-right font-mono text-xs">
          <GateStat label={to("Octo auto")} value={echoScore} />
          <GateStat label="Codex" value={codexScore} />
          <GateStat
            label={to("Ready")}
            value={
              radar.browser_desktop_quality.ready &&
              radar.parity_certification.ready &&
              readyDrafts
                ? 1
                : 0
            }
          />
        </div>
      </div>

      <div className="mt-2 grid gap-2 lg:grid-cols-[1.1fr_0.9fr]">
        <div className="rounded-md border border-border-default bg-muted/15 px-2 py-1.5">
          <div className="mb-1 text-xs font-medium text-muted-foreground">
            {to("Remaining automation edges")}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {topGaps.length === 0 ? (
              <Badge variant="outline" className="text-xs">
                {to("clear")}
              </Badge>
            ) : (
              topGaps.slice(0, 4).map((gap) => (
                <Badge key={gap.id} variant="outline" className="text-xs">
                  {gap.title} {gap.scores.echo}
                </Badge>
              ))
            )}
          </div>
        </div>
        <div className="rounded-md border border-border-default bg-muted/15 px-2 py-1.5">
          <div className="mb-1 flex items-center justify-between gap-2">
            <div className="min-w-0 truncate text-xs font-medium text-muted-foreground">
              {to("Signed automation rule drafts")}
            </div>
            <Badge variant="outline" className="shrink-0 text-xs">
              {drafts.verified}/{drafts.total}
            </Badge>
          </div>
          {topDraft ? (
            <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <div className="truncate font-mono text-xs">
                  {topDraft.signed_payload.rule.tool}
                </div>
                <div className="truncate text-xs text-muted-foreground">
                  {topDraft.signed_payload.rule.reason}
                </div>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-7 shrink-0 px-2 text-xs"
                disabled={
                  busyId ===
                  `install-automation-policy-rule:${topDraft.draft_id}`
                }
                onClick={() => onInstallDraft(topDraft.draft_id)}
              >
                <ShieldAlertIcon
                  className={cn(
                    "mr-1.5 size-3",
                    busyId ===
                      `install-automation-policy-rule:${topDraft.draft_id}` &&
                      "animate-spin",
                  )}
                />
                {to("Install deny rule")}
              </Button>
            </div>
          ) : (
            <div className="text-xs text-muted-foreground">
              {to("No automation rule drafts available.")}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
