import {
  ArchiveIcon,
  GitBranchIcon,
  GlobeIcon,
  ListChecksIcon,
  MonitorIcon,
  RefreshCwIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  type AgentTraceReplayGate,
  type AgentTraceReviewQueueItem,
  type BrowserDesktopQualityReport,
  type BrowserDesktopRepairRecipesReport,
  type BrowserDesktopRepairRecipeVerificationsReport,
  type ReplayEvidenceHint,
} from "@/core/agent-trace/api";
import { cn } from "@/lib/utils";

// ═══════════════════════════════════════════════════════════
// Shared replay components · used by both AgentOperatorPanel
// (inside /workspace/observability) and the standalone
// observability and agent-operator surfaces. Extracted here so both surfaces
// stay in sync without duplicating the rendering logic.
// ═══════════════════════════════════════════════════════════

export function StatusDot({ status }: { status?: string | null }) {
  const cls =
    status === "completed"
      ? "bg-success"
      : status === "failed"
        ? "bg-destructive"
        : status === "running"
          ? "bg-info"
          : "bg-muted-foreground/40";
  return <span className={cn("size-2 shrink-0 rounded-full", cls)} />;
}

export function priorityClass(priority: string) {
  if (priority === "P0") return "bg-destructive text-destructive-foreground";
  if (priority === "P1") return "bg-warning text-white";
  return "bg-muted text-muted-foreground";
}

export function GateStat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="text-muted-foreground">{label}</div>
      <div className="text-sm font-semibold">{value}</div>
    </div>
  );
}

export function MiniStat({
  label,
  value,
}: {
  label: string;
  value: number | string;
}) {
  return (
    <div className="rounded-md border border-background/70 bg-background/50 px-2 py-1.5">
      <div className="text-xs uppercase text-muted-foreground">{label}</div>
      <div className="text-xs font-semibold">{value}</div>
    </div>
  );
}

export function ReplayMetadataBadge({
  item,
}: {
  item: AgentTraceReviewQueueItem;
}) {
  const metadata = item.metadata ?? {};
  const schema = typeof metadata.schema === "string" ? metadata.schema : "";
  const isBrowser = schema.includes("browser_session");
  const isComputer = schema.includes("computer_activity");
  const label = isBrowser ? "browser" : isComputer ? "desktop" : "replay";
  const count =
    typeof metadata.action_count === "number"
      ? metadata.action_count
      : typeof metadata.activity_count === "number"
        ? metadata.activity_count
        : null;
  return (
    <Badge variant="outline" className="text-xs">
      {label}
      {count !== null ? ` ${count}` : ""}
    </Badge>
  );
}

// ─── ReplayGateCard ───────────────────────────────────────

export function ReplayGateCard({
  gate,
}: {
  gate: AgentTraceReplayGate | null;
}) {
  const blocked = gate && !gate.passed;
  const summary = gate?.summary;
  return (
    <div
      className={cn(
        "mt-3 rounded-lg border px-3 py-2",
        gate?.passed
          ? "border-success/25 bg-success/10"
          : blocked
            ? "border-destructive/30 bg-destructive/10"
            : "border-border-default bg-muted/15",
      )}
    >
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            <StatusDot
              status={
                gate?.passed ? "completed" : blocked ? "failed" : "unknown"
              }
            />
            Replay gate
            <Badge variant="outline" className="text-xs">
              {gate ? (gate.passed ? "passed" : "blocked") : "loading"}
            </Badge>
          </div>
          <div className="mt-1 truncate text-xs text-muted-foreground">
            {gate?.reason || "Waiting for replay evaluations"}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2 text-right font-mono text-xs sm:grid-cols-4">
          <GateStat label="cases" value={summary?.total ?? 0} />
          <GateStat label="pass" value={summary?.passed ?? 0} />
          <GateStat label="fail" value={summary?.failed ?? 0} />
          <GateStat label="low" value={summary?.below_min_score ?? 0} />
        </div>
      </div>
      {blocked && gate.failing_cases.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {gate.failing_cases.slice(0, 3).map((item, index) => (
            <Badge
              key={`${item.case_id ?? index}`}
              variant="outline"
              className="text-xs"
            >
              {String(item.case_id ?? "case")}
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── BrowserDesktopReplayReviewCard ──────────────────────

export function BrowserDesktopReplayReviewCard({
  items,
  total,
  quality,
  repairRecipes,
  repairVerifications,
  browserBusy,
  desktopBusy,
  recipeBusy,
  rerunBusy,
  staleBusy,
  onQueueBrowser,
  onQueueDesktop,
  onQueueRepairRecipes,
  onRerunBlocked,
  onRejectStale,
}: {
  items: AgentTraceReviewQueueItem[];
  total: number;
  quality: BrowserDesktopQualityReport;
  repairRecipes: BrowserDesktopRepairRecipesReport;
  repairVerifications: BrowserDesktopRepairRecipeVerificationsReport;
  browserBusy: boolean;
  desktopBusy: boolean;
  recipeBusy: boolean;
  rerunBusy: boolean;
  staleBusy: boolean;
  onQueueBrowser: () => void;
  onQueueDesktop: () => void;
  onQueueRepairRecipes: () => void;
  onRerunBlocked: () => void;
  onRejectStale: () => void;
}) {
  const topRecipe = repairRecipes.recipes[0];
  const trends = quality.replay_trends;
  const staleCount = trends.stale_source_artifact_count ?? 0;
  const reviewRate = Math.round((trends.review_rate ?? 0) * 100);
  return (
    <div className="mt-3 rounded-lg border border-info/20 bg-info/5 px-3 py-2">
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            <ListChecksIcon className="size-4 text-info dark:text-info" />
            Browser/Desktop replay review
            <Badge variant="outline" className="text-xs">
              {total} pending
            </Badge>
            <Badge variant="outline" className="text-xs">
              {repairRecipes.recipe_count} recipes
            </Badge>
            <Badge
              variant={repairVerifications.ready ? "outline" : "destructive"}
              className="text-xs"
            >
              {repairVerifications.verified_count}/{repairVerifications.total}{" "}
              verified
            </Badge>
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            Browser sessions and desktop actions that produced replay evidence
            for operator review.
            {` Review rate ${reviewRate}%; stale artifacts ${staleCount}.`}
            {topRecipe?.title ? ` Top recipe: ${topRecipe.title}.` : ""}
            {repairVerifications.blocked_count > 0
              ? ` ${repairVerifications.blocked_count} recipe(s) need rerun evidence.`
              : ""}
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Badge
            variant="outline"
            className={cn(
              "text-xs",
              total > 0
                ? "border-warning/30 bg-warning/10 text-warning"
                : "border-success/25 bg-success/10 text-success",
            )}
          >
            {total > 0 ? "operator action needed" : "clear"}
          </Badge>
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs"
            disabled={browserBusy}
            onClick={onQueueBrowser}
          >
            <GlobeIcon
              className={cn("mr-1.5 size-3", browserBusy && "animate-spin")}
            />
            Queue browser
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs"
            disabled={desktopBusy}
            onClick={onQueueDesktop}
          >
            <MonitorIcon
              className={cn("mr-1.5 size-3", desktopBusy && "animate-spin")}
            />
            Queue desktop
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs"
            disabled={recipeBusy || repairRecipes.recipe_count === 0}
            onClick={onQueueRepairRecipes}
          >
            <GitBranchIcon
              className={cn("mr-1.5 size-3", recipeBusy && "animate-spin")}
            />
            Queue recipes
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs"
            disabled={rerunBusy || repairVerifications.blocked_count === 0}
            onClick={onRerunBlocked}
            aria-label="Rerun blocked browser and desktop repair evidence"
          >
            <RefreshCwIcon
              className={cn("mr-1.5 size-3", rerunBusy && "animate-spin")}
            />
            Rerun blocked
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs"
            disabled={staleBusy || staleCount === 0}
            onClick={onRejectStale}
          >
            <ArchiveIcon
              className={cn("mr-1.5 size-3", staleBusy && "animate-spin")}
            />
            Clear stale
          </Button>
        </div>
      </div>

      <div className="mt-2 grid gap-2 sm:grid-cols-4">
        <MiniStat label="pending" value={trends.pending_count} />
        <MiniStat label="reviewed" value={trends.reviewed_count} />
        <MiniStat label="stale" value={staleCount} />
        <MiniStat label="review rate" value={`${reviewRate}%`} />
      </div>

      <div className="mt-2 grid gap-2 lg:grid-cols-2">
        {items.length === 0 ? (
          <div className="rounded-md border border-background/70 bg-background/60 px-2 py-2 text-xs text-muted-foreground">
            No browser or desktop replay cases are waiting.
          </div>
        ) : (
          items.map((item) => (
            <div
              key={item.id}
              className="rounded-md border border-background/70 bg-background/65 px-2 py-1.5"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0 truncate text-xs font-medium">
                  {item.title}
                </div>
                <Badge
                  className={cn(
                    "shrink-0 text-xs",
                    priorityClass(item.priority),
                  )}
                >
                  {item.priority}
                </Badge>
              </div>
              <div className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
                {item.text}
              </div>
              <div className="mt-1 flex flex-wrap gap-1.5">
                <ReplayMetadataBadge item={item} />
                <Badge variant="outline" className="text-xs">
                  {item.candidate_kind}
                </Badge>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

// ─── ReplayEvidenceDrilldownCard ─────────────────────────

export function ReplayEvidenceDrilldownCard({
  evidence,
  busy,
  onQueue,
}: {
  evidence: ReplayEvidenceHint;
  busy: boolean;
  onQueue: () => void;
}) {
  const caseId = evidence.case_id || "replay evidence";
  const fingerprint = evidence.fingerprint || "";
  return (
    <div className="mb-3 rounded-lg border border-info/25 bg-info/10 px-3 py-2">
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs font-semibold text-info dark:text-info">
            <ListChecksIcon className="size-3.5" />
            Replay evidence available
            <Badge variant="outline" className="text-xs">
              {evidence.replay_ready === false ? "not ready" : "ready"}
            </Badge>
          </div>
          <div className="mt-1 truncate text-xs text-muted-foreground">
            {caseId}
          </div>
          {fingerprint && (
            <div className="mt-0.5 font-mono text-xs text-muted-foreground">
              fp {fingerprint}
            </div>
          )}
          <div className="mt-1 flex flex-wrap gap-1.5">
            {evidence.replay_case_url && (
              <Badge variant="outline" className="text-xs">
                {evidence.replay_case_url}
              </Badge>
            )}
            {evidence.queue_url && (
              <Badge variant="outline" className="text-xs">
                {evidence.queue_url}
              </Badge>
            )}
          </div>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="h-7 shrink-0 px-2 text-xs"
          disabled={busy || !evidence.queue_url}
          onClick={onQueue}
        >
          <ListChecksIcon
            className={cn("mr-1.5 size-3", busy && "animate-spin")}
          />
          Queue evidence
        </Button>
      </div>
    </div>
  );
}
