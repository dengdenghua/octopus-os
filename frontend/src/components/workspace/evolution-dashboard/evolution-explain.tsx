import React, { useMemo } from "react";
import {
  ActivityIcon,
  AlertTriangleIcon,
  TrendingUpIcon,
  TrendingDownIcon,
  MinusIcon,
  ShieldIcon,
  BrainCircuitIcon,
} from "lucide-react";

import {
  useFitness,
  useDrift,
  useSkillPerformance,
} from "@/core/evolution/hooks";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

function l1ScoreColor(score: number): string {
  if (score >= 0.8) return "text-success";
  if (score >= 0.5) return "text-warning";
  return "text-destructive";
}

function l1BarColor(score: number): string {
  if (score >= 0.8) return "text-success";
  if (score >= 0.5) return "text-warning";
  return "text-destructive";
}

function verdictStyle(verdict: string): string {
  switch (verdict) {
    case "healthy":
      return "bg-success/15 text-success border-success/30";
    case "degraded":
      return "bg-warning/15 text-warning border-warning/30";
    case "unhealthy":
      return "bg-chart-7/15 text-chart-7 dark:text-chart-7 border-chart-7/30";
    case "critical":
      return "bg-destructive/15 text-destructive border-destructive/30";
    default:
      return "bg-muted text-muted-foreground border-border";
  }
}

function trendIcon(trend: string): React.ReactElement {
  switch (trend) {
    case "improving":
      return <TrendingUpIcon className="size-3.5 text-success" />;
    case "regressing":
      return <TrendingDownIcon className="size-3.5 text-destructive" />;
    default:
      return <MinusIcon className="size-3.5 text-muted-foreground" />;
  }
}

function actionBadgeStyle(action: string): string {
  switch (action) {
    case "evolve":
      return "bg-chart-1/15 text-chart-1 dark:text-chart-1 border-chart-1/30";
    case "revert":
      return "bg-destructive/15 text-destructive border-destructive/30";
    case "hold":
      return "bg-info/15 text-info dark:text-info border-info/30";
    case "explore":
      return "bg-warning/15 text-warning border-warning/30";
    default:
      return "bg-muted text-muted-foreground border-border";
  }
}

function severityDotColor(severity: string): string {
  switch (severity) {
    case "info":
      return "bg-info";
    case "warning":
      return "bg-warning";
    case "critical":
      return "bg-destructive";
    default:
      return "bg-muted-foreground";
  }
}

function kindBadgeStyle(kind: string): string {
  switch (kind) {
    case "soul_change":
      return "bg-chart-1/15 text-chart-1 dark:text-chart-1 border-chart-1/30";
    case "genome_change":
      return "bg-info/15 text-info dark:text-info border-info/30";
    case "score_regression":
      return "bg-destructive/15 text-destructive border-destructive/30";
    default:
      return "bg-muted text-muted-foreground border-border";
  }
}

function statusLabel(rate: number): string {
  if (rate >= 0.8) return "Excellent";
  if (rate >= 0.6) return "Good";
  if (rate >= 0.4) return "Declining";
  return "Critical";
}

function statusStyle(rate: number): string {
  if (rate >= 0.8)
    return "bg-success/15 text-success border-success/30";
  if (rate >= 0.6)
    return "bg-info/15 text-info dark:text-info border-info/30";
  if (rate >= 0.4)
    return "bg-warning/15 text-warning border-warning/30";
  return "bg-destructive/15 text-destructive border-destructive/30";
}

function successRateBarColor(rate: number): string {
  if (rate >= 0.8) return "bg-success";
  if (rate >= 0.6) return "bg-info";
  if (rate >= 0.4) return "bg-warning";
  return "bg-destructive";
}

function numberOrZero(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function fixed(value: unknown, digits: number): string {
  return numberOrZero(value).toFixed(digits);
}

export function FitnessExplainCard({ agentId }: { agentId?: string }) {
  const { t } = useI18n();
  const { data, isLoading } = useFitness(agentId);

  if (!agentId) {
    return (
      <div className="rounded-lg border bg-card p-4">
        <div className="flex items-center gap-2 mb-3">
          <ActivityIcon className="size-4 text-muted-foreground" />
          <span className="text-sm font-medium">
            {t.evolutionExplain.fitnessTitle}
          </span>
        </div>
        <p className="text-xs text-muted-foreground italic">
          {t.evolutionExplain.noAgentSelected}
        </p>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="rounded-lg border bg-card p-4">
        <div className="flex items-center gap-2 mb-3">
          <ActivityIcon className="size-4 text-muted-foreground" />
          <span className="text-sm font-medium">
            {t.evolutionExplain.fitnessTitle}
          </span>
        </div>
        <div className="flex h-24 items-center justify-center">
          <span className="text-xs text-muted-foreground">
            {t.evolutionExplain.loading}
          </span>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="rounded-lg border bg-card p-4">
        <div className="flex items-center gap-2 mb-3">
          <ActivityIcon className="size-4 text-muted-foreground" />
          <span className="text-sm font-medium">
            {t.evolutionExplain.fitnessTitle}
          </span>
        </div>
        <p className="text-xs text-muted-foreground italic">
          {t.evolutionExplain.noFitnessData}
        </p>
      </div>
    );
  }

  const l1 = data.l1;
  const l2 = data.l2;
  const l1Score = numberOrZero(l1?.score);
  const l1Pct = l1 ? Math.round(l1Score * 100) : 0;

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-center gap-2 mb-3">
        <ActivityIcon className="size-4 text-muted-foreground" />
        <span className="text-sm font-medium">
          {t.evolutionExplain.fitnessTitle}
        </span>
      </div>

      {l1 && (
        <div className="space-y-2 mb-3">
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted-foreground">L1 Score</span>
            <div className="flex items-center gap-1.5">
              <span
                className={cn(
                  "text-sm font-semibold tabular-nums",
                  l1ScoreColor(l1Score),
                )}
              >
                {l1Pct}%
              </span>
              {trendIcon(l1.trend)}
            </div>
          </div>
          <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
            <div
              className={cn(
                "h-full rounded-full transition-all",
                l1BarColor(l1Score),
              )}
              style={{
                width: `${l1Pct}%`,
                backgroundColor: "currentColor",
                opacity: 0.7,
              }}
            />
          </div>
        </div>
      )}

      {l2 && (
        <div className="space-y-1.5 rounded-md border border-border-subtle bg-muted/30 px-3 py-2 mb-3">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">L2 Analysis</span>
            <span className="tabular-nums text-muted-foreground">
              {fixed(numberOrZero(l2.confidence) * 100, 0)}% confidence
            </span>
          </div>
          {l2.dominant_failure && (
            <div className="text-xs">
              <span className="text-muted-foreground">Failure: </span>
              <span className="font-medium">{l2.dominant_failure}</span>
            </div>
          )}
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "inline-flex items-center rounded-md border px-1.5 py-0.5 text-xs font-medium",
                actionBadgeStyle(l2.action),
              )}
            >
              {l2.action}
            </span>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between pt-2 border-t border-border-subtle">
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Combined</span>
          <span className="text-sm font-semibold tabular-nums">
            {fixed(numberOrZero(data.combined) * 100, 0)}%
          </span>
        </div>
        <span
          className={cn(
            "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium capitalize",
            verdictStyle(data.verdict),
          )}
        >
          {data.verdict}
        </span>
      </div>
    </div>
  );
}

export function DriftExplainCard({ agentId }: { agentId?: string }) {
  const { t } = useI18n();
  const { data, isLoading } = useDrift(agentId);

  if (!agentId) {
    return (
      <div className="rounded-lg border bg-card p-4">
        <div className="flex items-center gap-2 mb-3">
          <ShieldIcon className="size-4 text-muted-foreground" />
          <span className="text-sm font-medium">
            {t.evolutionExplain.driftTitle}
          </span>
        </div>
        <p className="text-xs text-muted-foreground italic">
          {t.evolutionExplain.noAgentSelected}
        </p>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="rounded-lg border bg-card p-4">
        <div className="flex items-center gap-2 mb-3">
          <ShieldIcon className="size-4 text-muted-foreground" />
          <span className="text-sm font-medium">
            {t.evolutionExplain.driftTitle}
          </span>
        </div>
        <div className="flex h-24 items-center justify-center">
          <span className="text-xs text-muted-foreground">
            {t.evolutionExplain.loading}
          </span>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="rounded-lg border bg-card p-4">
        <div className="flex items-center gap-2 mb-3">
          <ShieldIcon className="size-4 text-muted-foreground" />
          <span className="text-sm font-medium">
            {t.evolutionExplain.driftTitle}
          </span>
        </div>
        <p className="text-xs text-muted-foreground italic">
          {t.evolutionExplain.noDriftData}
        </p>
      </div>
    );
  }

  if (!data.has_drift) {
    return (
      <div className="rounded-lg border bg-card p-4">
        <div className="flex items-center gap-2 mb-3">
          <ShieldIcon className="size-4 text-muted-foreground" />
          <span className="text-sm font-medium">
            {t.evolutionExplain.driftTitle}
          </span>
        </div>
        <div className="flex items-center gap-2 text-success">
          <ShieldIcon className="size-4" />
          <span className="text-xs font-medium">
            {t.evolutionExplain.noDriftDetected}
          </span>
        </div>
      </div>
    );
  }

  const maxSeverity = data.max_severity;
  const bannerStyle =
    maxSeverity === "critical"
      ? "bg-destructive/10 text-destructive border-destructive/30"
      : maxSeverity === "warning"
        ? "bg-warning/10 text-warning border-warning/30"
        : "bg-info/10 text-info dark:text-info border-info/30";

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-center gap-2 mb-3">
        <ShieldIcon className="size-4 text-muted-foreground" />
        <span className="text-sm font-medium">
          {t.evolutionExplain.driftTitle}
        </span>
      </div>

      <div
        className={cn(
          "mb-3 flex items-center gap-2 rounded-md border px-3 py-2 text-xs font-medium",
          bannerStyle,
        )}
      >
        <AlertTriangleIcon className="size-3.5" />
        {t.evolutionExplain.driftDetected(maxSeverity)}
      </div>

      <ul className="space-y-2">
        {data.events.map((evt, i) => (
          <li
            key={`${evt.kind}-${i}`}
            className="flex items-start gap-2 rounded-md border border-border-subtle bg-muted/30 px-3 py-2"
          >
            <span
              className={cn(
                "mt-0.5 size-2 shrink-0 rounded-full",
                severityDotColor(evt.severity),
              )}
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 mb-0.5">
                <span
                  className={cn(
                    "inline-flex items-center rounded-md border px-1.5 py-0.5 text-xs font-medium",
                    kindBadgeStyle(evt.kind),
                  )}
                >
                  {evt.kind.replace(/_/g, " ")}
                </span>
              </div>
              <p className="text-xs text-muted-foreground leading-relaxed">
                {evt.detail}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function VariantComparisonTable() {
  const { t } = useI18n();
  const { data, isLoading } = useSkillPerformance();

  const sorted = useMemo(() => {
    if (!data) return [];
    return [...data].sort(
      (a, b) => numberOrZero(a.success_rate) - numberOrZero(b.success_rate),
    );
  }, [data]);

  if (isLoading) {
    return (
      <div className="rounded-lg border bg-card p-4">
        <div className="flex items-center gap-2 mb-3">
          <BrainCircuitIcon className="size-4 text-muted-foreground" />
          <span className="text-sm font-medium">
            {t.evolutionExplain.variantTitle}
          </span>
        </div>
        <div className="flex h-24 items-center justify-center">
          <span className="text-xs text-muted-foreground">
            {t.evolutionExplain.loading}
          </span>
        </div>
      </div>
    );
  }

  if (!data || data.length === 0) {
    return (
      <div className="rounded-lg border bg-card p-4">
        <div className="flex items-center gap-2 mb-3">
          <BrainCircuitIcon className="size-4 text-muted-foreground" />
          <span className="text-sm font-medium">
            {t.evolutionExplain.variantTitle}
          </span>
        </div>
        <p className="text-xs text-muted-foreground italic">
          {t.evolutionExplain.noVariantData}
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-center gap-2 mb-3">
        <BrainCircuitIcon className="size-4 text-muted-foreground" />
        <span className="text-sm font-medium">
          {t.evolutionExplain.variantTitle}
        </span>
      </div>
      <div className="rounded-md border border-border-subtle overflow-hidden">
        <table className="w-full text-xs">
          <thead className="bg-muted/50 text-muted-foreground">
            <tr>
              <th className="text-left px-2 py-1">
                {t.evolutionExplain.colName}
              </th>
              <th className="text-right px-2 py-1">
                {t.evolutionExplain.colUsage}
              </th>
              <th className="text-left px-2 py-1">
                {t.evolutionExplain.colSuccessRate}
              </th>
              <th className="text-right px-2 py-1">
                {t.evolutionExplain.colStatus}
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((s) => {
              const successRate = numberOrZero(s.success_rate);
              const pct = Math.round(successRate * 100);
              return (
                <tr key={s.name} className="border-t border-border-subtle">
                  <td className="px-2 py-1 font-medium">{s.name}</td>
                  <td className="px-2 py-1 text-right tabular-nums">
                    {s.usage_count}
                  </td>
                  <td className="px-2 py-1">
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 flex-1 rounded-full bg-muted overflow-hidden">
                        <div
                          className={cn(
                            "h-full rounded-full",
                            successRateBarColor(successRate),
                          )}
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                      <span className="tabular-nums text-muted-foreground w-8 text-right">
                        {pct}%
                      </span>
                    </div>
                  </td>
                  <td className="px-2 py-1 text-right">
                    <span
                      className={cn(
                        "inline-flex items-center rounded-md border px-1.5 py-0.5 text-xs font-medium",
                        statusStyle(successRate),
                      )}
                    >
                      {statusLabel(successRate)}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function EvolutionExplain({ agentId }: { agentId?: string }) {
  return (
    <div className="space-y-4">
      <FitnessExplainCard agentId={agentId} />
      <DriftExplainCard agentId={agentId} />
      <VariantComparisonTable />
    </div>
  );
}
