import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AgentTracePolicyReviewRuleDrafts } from "@/core/agent-trace/api";
import { cn } from "@/lib/utils";
import { GateStat } from "../../replay-panel";
import { CheckCircle2Icon, ShieldAlertIcon } from "lucide-react";
import { shortId } from "../operator-utils";
import { useOperatorCopy } from "../use-operator-copy";

export function PolicyReviewRuleDraftCard({
  report,
  busyId,
  onInstall,
}: {
  report: AgentTracePolicyReviewRuleDrafts;
  busyId: string | null;
  onInstall: (draftId: string) => void;
}) {
  const to = useOperatorCopy();
  const drafts = report.drafts ?? [];
  const hasDrafts = drafts.length > 0;
  return (
    <div
      className={cn(
        "mt-3 rounded-lg border px-3 py-2",
        hasDrafts
          ? "border-success/25 bg-success/10"
          : "border-border-default bg-muted/15",
      )}
    >
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            <ShieldAlertIcon
              className={cn(
                "size-4",
                hasDrafts
                  ? "text-success"
                  : "text-muted-foreground",
              )}
            />
            {to("Policy review rules")}
            <Badge variant="outline" className="text-xs">
              {report.verified}/{report.total} {to("signed")}
            </Badge>
          </div>
          <div className="mt-1 truncate text-xs text-muted-foreground">
            {hasDrafts
              ? to(
                  "Replay-backed policy reviews produced signed install drafts",
                )
              : to("No signed policy-review rule drafts yet")}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2 text-right font-mono text-xs">
          <GateStat label={to("drafts")} value={report.total} />
          <GateStat label={to("signed")} value={report.verified} />
        </div>
      </div>
      {hasDrafts && (
        <div className="mt-2 space-y-1.5">
          {drafts.slice(0, 2).map((draft) => {
            const rule = draft.signed_payload.rule ?? {};
            const signature = draft.signature.digest ?? "";
            const installing =
              busyId === `install-policy-rule:${draft.draft_id}`;
            return (
              <div
                key={draft.draft_id}
                className="rounded-md border border-background/70 bg-background/60 px-2 py-1.5"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0 truncate text-xs font-medium">
                    {rule.effect ?? "deny"} {rule.tool ?? "tool"}
                  </div>
                  <Badge variant="outline" className="shrink-0 text-xs">
                    {shortId(signature || draft.draft_id)}
                  </Badge>
                </div>
                <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                  {rule.reason || to("Replay-backed policy review rule")}
                </div>
                <div className="mt-2 flex justify-end">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-7 px-2 text-xs"
                    disabled={installing}
                    onClick={() => onInstall(draft.draft_id)}
                  >
                    <CheckCircle2Icon className="mr-1.5 size-3.5" />
                    {to("Install signed rule")}
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
