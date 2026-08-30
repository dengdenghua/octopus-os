import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  ActivityIcon,
  AlertTriangleIcon,
  CheckCircle2Icon,
  GlobeIcon,
  ShieldCheckIcon,
} from "lucide-react";

import {
  E2E_SURPASS_TARGET_SCORE,
  fetchAgentCompetitorScorecard,
  fetchBrowserDesktopQuality,
  type AgentCompetitorScorecard,
  type BrowserDesktopQualityReport,
} from "@/core/agent-trace/api";
import { cn } from "@/lib/utils";

type CapabilitySurface = "browser" | "knowledge" | "integrations" | "skills";

interface CapabilityQualityStripProps {
  surface: CapabilitySurface;
  includeBrowserDesktop?: boolean;
  compact?: boolean;
  className?: string;
}

const SURFACE_COPY: Record<
  CapabilitySurface,
  { title: string; description: string }
> = {
  browser: {
    title: "浏览器与桌面能力",
    description: "真实浏览器、桌面自动化和 replay 治理不只服务 code mode。",
  },
  knowledge: {
    title: "知识与记忆能力",
    description: "记忆、证据和治理状态会影响知识库、研究和普通对话。",
  },
  integrations: {
    title: "插件与连接能力",
    description: "插件、MCP 和工具生态共用同一套证据化治理状态。",
  },
  skills: {
    title: "技能生态能力",
    description: "技能发现、启用和复用会受共享 scorecard 与证据覆盖约束。",
  },
};

export function CapabilityQualityStrip({
  surface,
  includeBrowserDesktop = false,
  compact = false,
  className,
}: CapabilityQualityStripProps) {
  const [scorecard, setScorecard] = useState<AgentCompetitorScorecard | null>(
    null,
  );
  const [browserQuality, setBrowserQuality] =
    useState<BrowserDesktopQualityReport | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [scorecardResult, browserResult] = await Promise.all([
          fetchAgentCompetitorScorecard(E2E_SURPASS_TARGET_SCORE),
          includeBrowserDesktop
            ? fetchBrowserDesktopQuality()
            : Promise.resolve(null),
        ]);
        if (!cancelled) {
          setScorecard(scorecardResult);
          setBrowserQuality(browserResult);
          setFailed(false);
        }
      } catch {
        if (!cancelled) {
          setFailed(true);
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [includeBrowserDesktop]);

  const summary = useMemo(() => {
    if (!scorecard) {
      return null;
    }
    const overall = Math.round(scorecard.overall?.echo ?? 0);
    const evidenceAdjusted = Math.round(
      scorecard.evidence_adjusted_overall?.echo ?? overall,
    );
    const belowTarget = scorecard.echo_below_target?.length ?? 0;
    const externalGaps = scorecard.echo_external_gap_dimensions?.length ?? 0;
    const behavioral = scorecard.evidence_layers?.behavioral_head_to_head;
    const targetScore = scorecard.target_score || E2E_SURPASS_TARGET_SCORE;
    const ecosystem = scorecard.ecosystem_readiness
      ? Math.round(scorecard.ecosystem_readiness.score * 100)
      : null;
    const browser = browserQuality
      ? {
          score: Math.round((browserQuality.score || 0) * 100),
          stale: browserQuality.replay_trends.stale_source_artifact_count ?? 0,
          reviewRate: Math.round(
            (browserQuality.replay_trends.review_rate || 0) * 100,
          ),
        }
      : null;
    return {
      overall,
      evidenceAdjusted,
      belowTarget,
      externalGaps,
      behavioral,
      ecosystem,
      browser,
      targetScore,
    };
  }, [browserQuality, scorecard]);

  const copy = SURFACE_COPY[surface];
  const ready =
    summary &&
    summary.evidenceAdjusted >= summary.targetScore &&
    summary.belowTarget === 0 &&
    summary.externalGaps === 0 &&
    (!summary.behavioral || summary.behavioral.ready) &&
    (!summary.browser || summary.browser.stale === 0);

  return (
    <section
      className={cn(
        compact
          ? "workspace-panel-subtle flex min-h-10 items-center gap-2 rounded-lg border border-border-default bg-background/75 px-3 py-1.5"
          : "workspace-panel-subtle flex flex-col gap-3 rounded-lg border border-border-default bg-background/75 px-4 py-3 md:flex-row md:items-center md:justify-between",
        className,
      )}
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <span
            className={cn(
              compact
                ? "flex size-6 items-center justify-center rounded-md"
                : "flex size-7 items-center justify-center rounded-lg",
              ready
                ? "bg-success/10 text-success"
                : "bg-primary/10 text-primary",
            )}
          >
            {ready ? (
              <ShieldCheckIcon className="size-4" />
            ) : (
              <ActivityIcon className="size-4" />
            )}
          </span>
          {copy.title}
        </div>
        {!compact ? (
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {copy.description}
          </p>
        ) : null}
      </div>

      <div
        className={cn(
          "flex flex-wrap items-center gap-2 text-xs",
          compact && "ml-auto shrink-0",
        )}
      >
        {summary ? (
          <>
            {compact ? (
              <QualityPill
                icon={<GlobeIcon className="size-3.5" />}
                label="Browser"
                value={summary.browser ? String(summary.browser.score) : String(summary.overall)}
                tone={summary.browser && summary.browser.stale > 0 ? "warn" : "good"}
              />
            ) : null}
            {!compact ? (
              <QualityPill
                icon={<CheckCircle2Icon className="size-3.5" />}
                label="Overall"
                value={String(summary.overall)}
                tone={summary.overall >= 90 ? "good" : "warn"}
              />
            ) : null}
            {!compact ? (
              <QualityPill
                icon={<ShieldCheckIcon className="size-3.5" />}
                label="Evidence"
                value={String(summary.evidenceAdjusted)}
                tone={summary.evidenceAdjusted >= 90 ? "good" : "warn"}
              />
            ) : null}
            {!compact && summary.behavioral ? (
              <QualityPill
                icon={<ActivityIcon className="size-3.5" />}
                label="Behavior"
                value={
                  summary.behavioral.ready
                    ? "certified"
                    : summary.behavioral.blocker === "infrastructure"
                      ? "provider blocked"
                      : "pending"
                }
                tone={summary.behavioral.ready ? "good" : "warn"}
              />
            ) : null}
            {!compact && summary.ecosystem !== null ? (
              <QualityPill
                icon={<ActivityIcon className="size-3.5" />}
                label="Eco"
                value={`${summary.ecosystem}%`}
                tone={summary.ecosystem >= 90 ? "good" : "warn"}
              />
            ) : null}
            {!compact && summary.browser ? (
              <QualityPill
                icon={<GlobeIcon className="size-3.5" />}
                label="Browser"
                value={`${summary.browser.score} · stale ${summary.browser.stale}`}
                tone={summary.browser.stale === 0 ? "good" : "warn"}
              />
            ) : null}
          </>
        ) : failed ? (
          <QualityPill
            icon={<AlertTriangleIcon className="size-3.5" />}
            label="Status"
            value="暂不可用"
            tone="muted"
          />
        ) : (
          <QualityPill
            icon={<ActivityIcon className="size-3.5 animate-pulse" />}
            label="Status"
            value="读取中"
            tone="muted"
          />
        )}
      </div>
    </section>
  );
}

function QualityPill({
  icon,
  label,
  value,
  tone,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  tone: "good" | "warn" | "muted";
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-medium",
        tone === "good" &&
          "border-success/25 bg-success/10 text-success",
        tone === "warn" &&
          "border-warning/25 bg-warning/10 text-warning",
        tone === "muted" &&
          "border-border-default bg-muted/40 text-muted-foreground",
      )}
    >
      {icon}
      <span className="text-muted-foreground">{label}</span>
      <span>{value}</span>
    </span>
  );
}
