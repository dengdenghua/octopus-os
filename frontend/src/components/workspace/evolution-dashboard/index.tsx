import React, { useState } from "react";
import {
  BrainCircuitIcon,
  TrendingUpIcon,
  ActivityIcon,
  DatabaseIcon,
  Loader2Icon,
  BookOpenIcon,
  LightbulbIcon,
  RefreshCwIcon,
  ChevronDownIcon,
} from "lucide-react";

import {
  useEvolutionOverview,
  useLearningCurve,
  useSkillPerformance,
  useMemoryGrowth,
  useRecommendations,
  useEvolutionStory,
} from "@/core/evolution/hooks";
import type {
  EvolutionOverview,
  LearningCurvePoint,
  SkillPerformance,
  Recommendation,
  EvolutionStory,
} from "@/core/evolution/api";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import { GeneLockControlCard } from "@/components/workspace/gene-lock-badge";
import { Button } from "@/components/ui/button";

import { SparklineChart } from "./sparkline-chart";

function numberOrZero(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function fixed(value: unknown, digits: number): string {
  return numberOrZero(value).toFixed(digits);
}

function successRateClass(rate: number): string {
  if (rate >= 0.8) return "text-success";
  if (rate >= 0.5) return "text-warning";
  return "text-destructive";
}

export function EvolutionDashboard({ className }: { className?: string }) {
  const { t } = useI18n();
  const overviewQ = useEvolutionOverview();
  const learningCurveQ = useLearningCurve();
  const skillPerformanceQ = useSkillPerformance();
  const memoryGrowthQ = useMemoryGrowth();
  const recommendationsQ = useRecommendations();
  const storyQ = useEvolutionStory();

  const isLoading =
    overviewQ.isLoading ||
    learningCurveQ.isLoading ||
    skillPerformanceQ.isLoading ||
    memoryGrowthQ.isLoading ||
    recommendationsQ.isLoading ||
    storyQ.isLoading;

  const firstError =
    overviewQ.error ||
    learningCurveQ.error ||
    skillPerformanceQ.error ||
    memoryGrowthQ.error ||
    recommendationsQ.error ||
    storyQ.error;

  const retryAll = () => {
    void Promise.allSettled([
      overviewQ.refetch(),
      learningCurveQ.refetch(),
      skillPerformanceQ.refetch(),
      memoryGrowthQ.refetch(),
      recommendationsQ.refetch(),
      storyQ.refetch(),
    ]);
  };

  if (isLoading) {
    return (
      <div
        className={cn("flex min-h-56 items-center justify-center", className)}
        role="status"
        aria-live="polite"
      >
        <div className="flex flex-col items-center gap-3">
          <Loader2Icon className="size-8 animate-spin text-chart-1" />
          <span className="text-sm text-muted-foreground">
            {t.evolutionDashboard.loading}
          </span>
        </div>
      </div>
    );
  }

  if (firstError) {
    return (
      <section
        role="alert"
        className={cn(
          "flex min-h-[440px] flex-col items-center justify-center rounded-xl border border-border-subtle bg-gradient-to-b from-muted/20 to-background px-6 py-12 text-center",
          className,
        )}
      >
        <span className="flex size-12 items-center justify-center rounded-xl border border-destructive/20 bg-destructive/5 text-destructive">
          <BrainCircuitIcon className="size-5" aria-hidden="true" />
        </span>
        <h2 className="mt-4 text-base font-semibold text-foreground">
          {t.evolutionDashboard.connectionFailed}
        </h2>
        <p className="mt-2 max-w-lg text-sm leading-6 text-muted-foreground">
          {t.evolutionDashboard.noEvidenceDescription}
        </p>
        <div className="mt-5 grid w-full max-w-2xl gap-2 sm:grid-cols-3">
          {[
            {
              icon: ActivityIcon,
              title: t.evolutionDashboard.observeTasks,
              description: t.evolutionDashboard.observeTasksDescription,
            },
            {
              icon: BookOpenIcon,
              title: t.evolutionDashboard.formSkills,
              description: t.evolutionDashboard.formSkillsDescription,
            },
            {
              icon: LightbulbIcon,
              title: t.evolutionDashboard.proposeImprovements,
              description: t.evolutionDashboard.proposeImprovementsDescription,
            },
          ].map((item) => (
            <div
              key={item.title}
              className="rounded-lg border border-border-subtle bg-card/60 p-3 text-left"
            >
              <item.icon className="size-4 text-primary" aria-hidden="true" />
              <p className="mt-2 text-xs font-semibold text-foreground">
                {item.title}
              </p>
              <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
                {item.description}
              </p>
            </div>
          ))}
        </div>
        <div className="mt-5">
          <Button type="button" onClick={retryAll}>
            <RefreshCwIcon className="mr-1.5 size-3.5" aria-hidden="true" />
            {t.evolutionDashboard.retryLoading}
          </Button>
        </div>
      </section>
    );
  }

  const memorySparkline = memoryGrowthQ.data
    ? memoryGrowthQ.data.map(
        (d) => d.fact + d.preference + d.learned_skill + d.relationship,
      )
    : [];
  const overview = overviewQ.data ?? null;
  const learningCurve = learningCurveQ.data ?? [];
  const skillPerformance = skillPerformanceQ.data ?? [];
  const recommendations = recommendationsQ.data ?? [];
  const story = storyQ.data;
  const topSkills = [...skillPerformance]
    .filter(
      (skill) =>
        numberOrZero(skill.usage_count) > 0 ||
        numberOrZero(skill.success_rate) > 0,
    )
    .sort((a, b) => numberOrZero(b.usage_count) - numberOrZero(a.usage_count))
    .slice(0, 5);

  return (
    <div className={cn("space-y-5", className)}>
      <GrowthStoryHero
        overview={overview}
        story={story}
        memorySparkline={memorySparkline}
        recommendationCount={recommendations.length}
      />
      <div className="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
        <LearningStory data={learningCurve} />
        <SkillStory data={topSkills} />
      </div>
      <RecommendationsStory data={recommendations} />
    </div>
  );
}

export default EvolutionDashboard;

function formatPercent(value: unknown, digits = 0): string {
  return `${fixed(numberOrZero(value) * 100, digits)}%`;
}

function GrowthStoryHero({
  overview,
  story,
  memorySparkline,
  recommendationCount,
}: {
  overview: EvolutionOverview | null;
  story: EvolutionStory | null;
  memorySparkline: number[];
  recommendationCount: number;
}) {
  const { t } = useI18n();
  const [selectedInsightKey, setSelectedInsightKey] = useState<string | null>(
    null,
  );
  const skills = overview?.skills;
  const memory = overview?.memory;
  const observedCount = story?.observed_task_count ?? 0;
  const durableCount = story?.durable_change_count ?? 0;
  const lessonCount = (story?.rule_count ?? 0) + (story?.memory_count ?? 0);
  const hasRealChange = Boolean(story?.has_real_change && durableCount > 0);
  const totalSkills = numberOrZero(skills?.total);
  const autoSkills = numberOrZero(skills?.auto_extracted);
  const ruleCount = numberOrZero(memory?.categories?.rules);
  const stages = [
    {
      key: "tasks",
      icon: ActivityIcon,
      title: t.evolutionDashboard.observeTasks,
      value: observedCount,
      unit: t.evolutionDashboard.unitTimes,
      done: observedCount > 0,
      description: t.evolutionDashboard.observationsDescription,
    },
    {
      key: "memories",
      icon: DatabaseIcon,
      title: t.evolutionDashboard.accumulateMemories,
      value: lessonCount,
      unit: t.evolutionDashboard.unitItems,
      done: lessonCount > 0,
      description: t.evolutionDashboard.savedLessonsPlainDescription,
    },
    {
      key: "metric-skills",
      icon: BookOpenIcon,
      title: t.evolutionDashboard.formSkills,
      value: autoSkills,
      unit: t.evolutionDashboard.unitSkills,
      done: autoSkills > 0,
      description: t.evolutionDashboard.formSkillsDescription,
    },
    {
      key: "improvements",
      icon: LightbulbIcon,
      title: t.evolutionDashboard.proposeImprovements,
      value: recommendationCount,
      unit: t.evolutionDashboard.unitSuggestions,
      done: recommendationCount > 0,
      description: t.evolutionDashboard.proposeImprovementsDescription,
    },
  ];
  const metrics = [
    {
      key: "observed",
      icon: ActivityIcon,
      title: t.evolutionDashboard.observedTasks,
      value: observedCount,
      detail: t.evolutionDashboard.observedTasksPlainDescription,
    },
    {
      key: "metric-memories",
      icon: DatabaseIcon,
      title: t.evolutionDashboard.savedLessons,
      value: lessonCount,
      detail:
        ruleCount > 0
          ? t.evolutionDashboard.ruleMemoryCount(ruleCount)
          : t.evolutionDashboard.actualChangesEmptyTitle,
      sparkline: memorySparkline.length >= 2 ? memorySparkline : undefined,
    },
    {
      key: "skills",
      icon: BookOpenIcon,
      title: t.evolutionDashboard.autoExtractedSkills,
      value: autoSkills,
      detail:
        totalSkills > 0
          ? t.evolutionDashboard.autoExtractedSkillsShare(
              formatPercent(autoSkills / Math.max(totalSkills, 1)),
            )
          : t.evolutionDashboard.waitingForSkillAccumulation,
    },
  ];
  const learnedItems = [
    ...(story?.changes ?? []).map((change, index) => ({
      key: `change-${change.kind}-${index}`,
      source: change.title,
      content: change.content,
    })),
    ...(story?.observations ?? []).flatMap((observation) =>
      (observation.learning_points ?? []).map((content, index) => ({
        key: `observation-${observation.task_id}-${index}`,
        source: observation.title,
        content,
      })),
    ),
  ].slice(0, 4);
  const selectedStage = stages.find(
    (stage) => stage.key === selectedInsightKey,
  );
  const selectedMetric = metrics.find(
    (metric) => metric.key === selectedInsightKey,
  );
  const selectedInsight = selectedStage
    ? {
        title: selectedStage.title,
        detail: selectedStage.description,
        valueLabel: `${selectedStage.value}${selectedStage.unit}`,
      }
    : selectedMetric
      ? {
          title: selectedMetric.title,
          detail: selectedMetric.detail,
          valueLabel: String(selectedMetric.value),
        }
      : null;
  const toggleInsight = (key: string) => {
    setSelectedInsightKey((current) => (current === key ? null : key));
  };

  return (
    <section className="grid gap-4 xl:grid-cols-[1.05fr_0.95fr]">
      <div className="rounded-lg border border-border-default bg-gradient-to-br from-primary/10 via-background to-background p-5">
        <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              <BrainCircuitIcon className="size-4 text-primary" />
              {hasRealChange
                ? t.evolutionDashboard.storyRealChangeTitle(durableCount)
                : t.evolutionDashboard.storyNoRealChangeTitle}
            </h2>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground">
              {hasRealChange
                ? t.evolutionDashboard.storyRealChangeDescription(durableCount)
                : t.evolutionDashboard.storyNoRealChangeDescription(
                    observedCount,
                  )}
            </p>
          </div>
          <div className="shrink-0 rounded-lg border border-primary/20 bg-background/80 px-4 py-3 text-right shadow-[var(--shadow-xs)]">
            <div className="text-xs text-muted-foreground">
              {t.evolutionDashboard.changedBehaviors}
            </div>
            <div
              aria-label={`${t.evolutionDashboard.changedBehaviors}: ${durableCount}`}
              className={cn("mt-1 text-3xl font-bold tabular-nums", hasRealChange ? "text-success" : "text-warning")}
            >
              {durableCount}
            </div>
            <div className="text-xs text-muted-foreground">
              {t.evolutionDashboard.unitItems}
            </div>
          </div>
        </div>

        <ol className="mt-5 grid gap-3 md:grid-cols-4">
          {stages.map((stage, index) => (
            <EvolutionStage
              key={stage.title}
              stage={stage}
              index={index + 1}
              selected={selectedInsightKey === stage.key}
              onActivate={() => toggleInsight(stage.key)}
            />
          ))}
        </ol>

        {selectedInsight ? (
          <div
            className="mt-3 rounded-lg border border-primary/25 bg-background/80 px-4 py-3 shadow-[var(--shadow-xs)]"
            role="region"
            aria-live="polite"
            aria-label={selectedInsight.title}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-foreground">
                  {selectedInsight.title}
                </h3>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  {selectedInsight.detail}
                </p>
              </div>
              <span className="shrink-0 text-sm font-semibold tabular-nums text-primary">
                {selectedInsight.valueLabel}
              </span>
            </div>
          </div>
        ) : (
          <div className="mt-3 rounded-lg border border-border-subtle bg-background/70 px-4 py-3">
            <h3 className="text-sm font-semibold text-foreground">
              {t.evolutionDashboard.actualChangesTitle}
            </h3>
            {learnedItems.length ? (
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                {learnedItems.map((item) => (
                  <div
                    key={item.key}
                    className="rounded-md border border-border-subtle bg-card/70 px-3 py-2.5"
                  >
                    <p className="line-clamp-3 text-xs font-medium leading-5 text-foreground">
                      {item.content}
                    </p>
                    <p className="mt-1 truncate text-[11px] text-muted-foreground">
                      {item.source}
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {t.evolutionDashboard.actualChangesEmptyTitle}
              </p>
            )}
          </div>
        )}

        <GeneLockControlCard compact className="mt-3" />
      </div>

      <div className="grid h-fit gap-3 self-start sm:grid-cols-3 xl:grid-cols-3">
        {metrics.map(({ key, ...metric }) => (
          <StoryMetric
            key={key}
            {...metric}
            selected={selectedInsightKey === key}
            onActivate={() => toggleInsight(key)}
          />
        ))}
      </div>
    </section>
  );
}

function EvolutionStage({
  stage,
  index,
  selected,
  onActivate,
}: {
  stage: {
    key: string;
    icon: React.ElementType;
    title: string;
    value: number;
    unit: string;
    done: boolean;
    description: string;
  };
  index: number;
  selected: boolean;
  onActivate: () => void;
}) {
  const Icon = stage.icon;
  return (
    <li
      className={cn(
        "relative overflow-hidden rounded-lg border",
        selected && "ring-2 ring-primary/25",
        stage.done
          ? "border-primary/30 bg-primary/5"
          : "border-border-default bg-muted/25 text-muted-foreground",
      )}
    >
      <button
        type="button"
        className="w-full px-3 py-3 text-left transition-colors hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
        aria-expanded={selected}
        onClick={onActivate}
      >
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "flex size-6 items-center justify-center rounded-full text-xs font-semibold",
                stage.done
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted text-muted-foreground",
              )}
            >
              {index}
            </span>
            <Icon className={cn("size-3.5", stage.done && "text-primary")} />
          </div>
          <span className="ml-auto text-xs tabular-nums">
            {stage.value}
            {stage.unit}
          </span>
          <ChevronDownIcon
            className={cn(
              "size-3.5 text-muted-foreground transition-transform",
              selected && "rotate-180",
            )}
          />
        </div>
        <h3 className="mt-3 text-sm font-semibold">{stage.title}</h3>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          {stage.description}
        </p>
      </button>
    </li>
  );
}

function StoryMetric({
  icon: Icon,
  title,
  value,
  detail,
  sparkline,
  selected,
  onActivate,
}: {
  icon: React.ElementType;
  title: string;
  value: number;
  detail: string;
  sparkline?: number[];
  selected: boolean;
  onActivate: () => void;
}) {
  return (
    <button
      type="button"
      aria-expanded={selected}
      onClick={onActivate}
      className={cn(
        "rounded-lg border border-border-default bg-card px-4 py-3 text-left transition-all hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-[var(--shadow-xs)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40",
        selected && "border-primary/35 bg-primary/5 ring-2 ring-primary/20",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <Icon className="size-3.5" />
            {title}
          </h3>
          <div className="mt-2 text-2xl font-bold tabular-nums">{value}</div>
          <div className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {detail}
          </div>
        </div>
        <div className="flex shrink-0 items-start gap-1">
          {sparkline && (
            <SparklineChart
              data={sparkline}
              color="#3b82f6"
              width={64}
              height={24}
            />
          )}
          <ChevronDownIcon
            className={cn(
              "size-3.5 text-muted-foreground transition-transform",
              selected && "rotate-180",
            )}
          />
        </div>
      </div>
    </button>
  );
}

function LearningStory({ data }: { data: LearningCurvePoint[] }) {
  const { t } = useI18n();
  if (data.length === 0) {
    return (
      <section className="rounded-md border border-border-default bg-card p-3">
        <SectionTitle
          icon={TrendingUpIcon}
          title={t.evolutionDashboard.capabilityTrend}
        />
        <EmptyStory text={t.evolutionDashboard.noTrendYet} />
      </section>
    );
  }

  const compact = data.slice(-6);
  const first = compact[0];
  const last = compact[compact.length - 1];
  const firstRate = numberOrZero(first?.success_rate);
  const lastRate = numberOrZero(last?.success_rate);
  const delta = lastRate - firstRate;
  const hasTrend = compact.length > 1;
  const avgDuration =
    compact.reduce(
      (sum, point) => sum + numberOrZero(point.avg_duration_ms),
      0,
    ) / compact.length;

  return (
    <section className="rounded-md border border-border-default bg-card p-3">
      <div className="flex items-start justify-between gap-4">
        <SectionTitle
          icon={TrendingUpIcon}
          title={t.evolutionDashboard.capabilityTrend}
        />
        <div className="text-right">
          <div
            className={cn(
              "text-sm font-semibold tabular-nums",
              !hasTrend
                ? "text-muted-foreground"
                : delta >= 0
                  ? "text-success"
                  : "text-destructive",
            )}
          >
            {hasTrend
              ? `${delta >= 0 ? "+" : ""}${formatPercent(delta, 0)}`
              : "—"}
          </div>
          <div className="text-xs text-muted-foreground">
            {t.evolutionDashboard.recentChange}
          </div>
        </div>
      </div>

      <div className="mt-4 grid gap-2 sm:grid-cols-3">
        <MiniStat
          label={t.evolutionDashboard.currentSuccessRate}
          value={formatPercent(lastRate, 0)}
        />
        <MiniStat
          label={t.evolutionDashboard.avgDuration}
          value={
            avgDuration <= 0
              ? "—"
              : avgDuration >= 1000
                ? `${fixed(avgDuration / 1000, 1)}s`
                : `${Math.round(avgDuration)}ms`
          }
        />
        <MiniStat
          label={t.evolutionDashboard.recentSkillCalls}
          value={String(
            compact.reduce(
              (sum, point) => sum + numberOrZero(point.skills_used),
              0,
            ),
          )}
        />
      </div>

      <div
        className="mt-4 flex items-end gap-2 overflow-hidden rounded-lg border border-border-subtle bg-muted/20 px-3 py-3"
        role="list"
        aria-label={t.evolutionDashboard.capabilityTrend}
      >
        {compact.map((point) => {
          const rate = numberOrZero(point.success_rate);
          const ratePct = Math.round(Math.min(1, Math.max(0, rate)) * 100);
          return (
            <div
              key={point.week}
              className="flex min-w-0 flex-1 flex-col items-center gap-2"
              role="listitem"
              aria-label={`${point.week}: ${ratePct}%`}
            >
              <div className="flex h-20 w-full items-end justify-center">
                <div
                  className={cn(
                    "w-full max-w-8 rounded-t-md",
                    rate >= 0.8
                      ? "bg-success"
                      : rate >= 0.5
                        ? "bg-warning"
                        : "bg-destructive",
                  )}
                  style={{ height: `${Math.max(ratePct, 6)}%` }}
                />
              </div>
              <div className="truncate text-xs text-muted-foreground">
                {point.week}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function SkillStory({ data }: { data: SkillPerformance[] }) {
  const { t } = useI18n();
  if (data.length === 0) {
    return (
      <section className="rounded-md border border-border-default bg-card p-3">
        <SectionTitle
          icon={BrainCircuitIcon}
          title={t.evolutionDashboard.strongerSkills}
        />
        <EmptyStory text={t.evolutionDashboard.noSkillPerformanceYet} />
      </section>
    );
  }

  return (
    <section className="rounded-md border border-border-default bg-card p-3">
      <SectionTitle
        icon={BrainCircuitIcon}
        title={t.evolutionDashboard.strongerSkills}
      />
      <div className="mt-4 space-y-3">
        {data.map((skill) => {
          const rate = numberOrZero(skill.success_rate);
          const ratePct = Math.round(Math.min(1, Math.max(0, rate)) * 100);
          return (
            <div key={skill.name} className="space-y-1.5">
              <div className="flex items-center justify-between gap-3 text-xs">
                <span className="min-w-0 truncate font-medium">
                  {skill.name}
                </span>
                <span
                  className={cn(
                    "shrink-0 tabular-nums",
                    successRateClass(rate),
                  )}
                >
                  {formatPercent(rate)}
                </span>
              </div>
              <div
                className="h-2 overflow-hidden rounded-full bg-muted"
                role="progressbar"
                aria-label={`${skill.name} · ${t.evolutionDashboard.successRate}`}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={ratePct}
              >
                <div
                  className={cn(
                    "h-full rounded-full",
                    rate >= 0.8
                      ? "bg-success"
                      : rate >= 0.5
                        ? "bg-warning"
                        : "bg-destructive",
                  )}
                  style={{ width: `${Math.max(ratePct, 4)}%` }}
                />
              </div>
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>
                  {t.evolutionDashboard.skillCalls(
                    numberOrZero(skill.usage_count),
                  )}
                </span>
                <span>{skill.source}</span>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function RecommendationsStory({ data }: { data: Recommendation[] }) {
  const { t } = useI18n();
  const [expandedIndex, setExpandedIndex] = useState<number | null>(null);
  if (data.length === 0) {
    return (
      <section className="rounded-md border border-border-default bg-card p-3">
        <SectionTitle
          icon={LightbulbIcon}
          title={t.evolutionDashboard.howToImproveNext}
        />
        <EmptyStory text={t.evolutionDashboard.noPendingRecommendations} />
      </section>
    );
  }

  return (
    <section className="rounded-md border border-border-default bg-card p-3">
      <SectionTitle
        icon={LightbulbIcon}
        title={t.evolutionDashboard.howToImproveNext}
      />
      <div className="mt-3 grid gap-3 lg:grid-cols-3">
        {data.slice(0, 3).map((rec, index) => (
          <button
            type="button"
            key={`${rec.title}-${index}`}
            aria-expanded={expandedIndex === index}
            onClick={() =>
              setExpandedIndex((current) => (current === index ? null : index))
            }
            className="rounded-lg border border-border-default bg-muted/20 px-3 py-3 text-left transition-all hover:border-primary/30 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
          >
            <div className="flex items-center gap-2">
              <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
                {index + 1}
              </span>
              <h3 className="min-w-0 truncate text-sm font-semibold">
                {rec.title}
              </h3>
              <ChevronDownIcon
                className={cn(
                  "ml-auto size-3.5 shrink-0 text-muted-foreground transition-transform",
                  expandedIndex === index && "rotate-180",
                )}
              />
            </div>
            <p
              className={cn(
                "mt-2 text-xs leading-relaxed text-muted-foreground",
                expandedIndex !== index && "line-clamp-3",
              )}
            >
              {rec.description}
            </p>
          </button>
        ))}
      </div>
    </section>
  );
}

function SectionTitle({
  icon: Icon,
  title,
}: {
  icon: React.ElementType;
  title: string;
}) {
  return (
    <h2 className="flex items-center gap-2 text-sm font-semibold">
      <Icon className="size-4 text-primary" />
      {title}
    </h2>
  );
}

function EmptyStory({ text }: { text: string }) {
  return (
    <div className="mt-3 flex min-h-14 items-center rounded-md border border-dashed border-border-default bg-muted/20 px-3 py-3 text-xs leading-relaxed text-muted-foreground">
      {text}
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border bg-card px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-sm font-semibold tabular-nums">{value}</div>
    </div>
  );
}
