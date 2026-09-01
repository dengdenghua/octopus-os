import { Badge } from "@/components/ui/badge";
import type { PluginLifecycleHistory, PluginSmokeSummary } from "@/core/plugins/types";
import { cn } from "@/lib/utils";
import { GateStat } from "../../replay-panel";
import { ListChecksIcon } from "lucide-react";
import { useOperatorCopy } from "../use-operator-copy";

export function PluginHealthCard({
  summary,
  lifecycle,
}: {
  summary: PluginSmokeSummary;
  lifecycle: PluginLifecycleHistory;
}) {
  const to = useOperatorCopy();
  const risky =
    summary.failed_count > 0 ||
    summary.warning_count > 0 ||
    (summary.invalid_signature_count ?? 0) > 0;
  const compatibility = summary.compatibility;
  const rows =
    summary.failed.length > 0
      ? summary.failed
      : summary.review_required.length > 0
        ? summary.review_required
        : summary.warnings;
  const latestLifecycle = lifecycle.items.at(-1);
  return (
    <div
      className={cn(
        "mt-3 rounded-lg border px-3 py-2",
        summary.failed_count > 0
          ? "border-destructive/30 bg-destructive/10"
          : risky
            ? "border-warning/30 bg-warning/10"
            : "border-border-default bg-muted/15",
      )}
    >
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            <ListChecksIcon className="size-4 text-primary" />
            {to("Plugin health")}
            <Badge variant="outline" className="text-xs">
              {summary.ok_count}/{summary.total} {to("ok")}
            </Badge>
            {compatibility && (
              <Badge
                variant="outline"
                className={cn(
                  "text-xs",
                  compatibility.verdict === "fail"
                    ? "border-destructive/30 bg-destructive/10 text-destructive"
                    : compatibility.verdict === "review"
                      ? "border-warning/30 bg-warning/10 text-warning"
                      : "border-success/25 bg-success/10 text-success",
                )}
              >
                {to("compat")} {compatibility.verdict}
              </Badge>
            )}
          </div>
          <div className="mt-1 truncate text-xs text-muted-foreground">
            {summary.failed_count > 0
              ? to("Some plugins failed local smoke checks")
              : summary.review_required_count > 0
                ? to("Some local plugins need operator review")
                : to("Installed Codex plugins passed local smoke checks")}
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-right font-mono text-xs">
          <GateStat label={to("total")} value={summary.total} />
          <GateStat label={to("ok")} value={summary.ok_count} />
          <GateStat label={to("fail")} value={summary.failed_count} />
          <GateStat label={to("warn")} value={summary.warning_count} />
          <GateStat
            label="signed"
            value={summary.publisher_verified_count ?? 0}
          />
          {compatibility && (
            <GateStat label="compat" value={compatibility.passed} />
          )}
        </div>
      </div>
      {compatibility?.next_actions?.[0] && (
        <div className="mt-2 truncate text-xs text-muted-foreground">
          {compatibility.next_actions[0]}
        </div>
      )}
      <div className="mt-2 flex items-center justify-between gap-2 rounded-md border border-background/70 bg-background/60 px-2 py-1.5 text-xs">
        <span className="font-medium">{to("Lifecycle history")}</span>
        <span className="min-w-0 truncate text-muted-foreground">
          {latestLifecycle
            ? `${latestLifecycle.operation} ${latestLifecycle.plugin_id} · ${latestLifecycle.status}`
            : to("No install, upgrade, or rollback transactions")}
        </span>
        <Badge variant="outline" className="shrink-0 text-xs">
          {lifecycle.total} {to("tx")}
        </Badge>
      </div>
      {rows.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {rows.slice(0, 4).map((item, index) => (
            <Badge
              key={`${item.plugin_id ?? item.plugin_name ?? index}`}
              variant="outline"
              className={cn(
                "max-w-full text-xs",
                summary.failed_count > 0
                  ? "border-destructive/30 bg-destructive/10 text-destructive"
                  : "border-warning/30 bg-warning/10 text-warning",
              )}
            >
              <span className="truncate">
                {item.plugin_name ?? item.plugin_id ?? "plugin"}
              </span>
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}
